"""Lakeflow Spark Declarative Pipeline: mine-to-port ore blending.

bronze  : Auto Loader over the UC Volume landing zone (one streaming table per source system)
silver  : typed, de-duplicated, quality-checked entities (expectations drop bad records)
gold    : stockpile grade (block model + lab reconciliation), ML feature table per train,
          train performance vs spec (row-filtered by hub), vessel compliance (penalty $ masked)

All timestamps are AWST (naive).
"""
from pyspark import pipelines as dp
from pyspark.sql import Window
from pyspark.sql import functions as F

LANDING = spark.conf.get("landing_path", "/Volumes/jack_freeman_catalog/fmg_ore_blending/landing")
SCHEMA = spark.conf.get("target_schema_fqn", "jack_freeman_catalog.fmg_ore_blending")
AS_OF = spark.conf.get("as_of_ts", "2026-11-03 06:00:00")  # demo "now"
EL = ["fe", "sio2", "al2o3", "p", "loi"]


def autoload(sub, fmt, glob, hints=None):
    r = (spark.readStream.format("cloudFiles")
         .option("cloudFiles.format", fmt)
         .option("cloudFiles.inferColumnTypes", "true")
         .option("pathGlobFilter", glob))
    if fmt == "csv":
        r = r.option("header", "true")
    if hints:
        r = r.option("cloudFiles.schemaHints", hints)
    return (r.load(f"{LANDING}/{sub}")
            .withColumn("_source_file", F.col("_metadata.file_path"))
            .withColumn("_ingested_at", F.current_timestamp()))


# ----------------------------------------------------------------------------- bronze
@dp.table(comment="BRONZE fleet-management (FMS) haul cycles: truck dumps to ROM stockpiles with block-model grade estimates")
def bronze_haul_cycles():
    return autoload("fms", "csv", "haul_cycles_*.csv")


@dp.table(comment="BRONZE rail system train consists incl. reclaim mix per stockpile (JSON)")
def bronze_train_consists():
    return autoload("rail", "json", "train_consists_*.json")


@dp.table(comment="BRONZE planned trains for the current shift (no reclaim plan yet)")
def bronze_train_plan():
    return autoload("rail", "csv", "train_plan_*.csv")


@dp.table(comment="BRONZE cross-belt analyser (PGNAA) readings, 1/min while a train loads")
def bronze_xba_readings():
    return autoload("analyser", "csv", "xba_readings_*.csv")


@dp.table(comment="BRONZE LIMS lab assays (train composites), nested analyte array")
def bronze_lims_assays():
    return autoload("lims", "json", "assays_*.json")


@dp.table(comment="BRONZE port system vessel cargoes (which trains loaded each vessel)")
def bronze_vessel_cargoes():
    return autoload("port", "json", "vessel_cargoes_*.json")


# ------------------------------------------------------------------------- reference
def ref_csv(name):
    return spark.read.option("header", "true").option("inferSchema", "true").csv(f"{LANDING}/reference/{name}.csv")


@dp.materialized_view(comment="REF ROM stockpiles: hub, grade class, product, design grade")
def ref_stockpiles():
    return ref_csv("stockpiles")


@dp.materialized_view(comment="REF mine hubs and train load-outs")
def ref_mine_hubs():
    return ref_csv("mine_hubs")


@dp.materialized_view(comment="REF product specifications (illustrative typical/min/max limits)")
def ref_product_specs():
    return ref_csv("product_specs")


@dp.materialized_view(comment="REF illustrative grade penalty schedule (USD per dry metric tonne)")
def ref_penalty_schedule():
    return ref_csv("penalty_schedule")


@dp.materialized_view(comment="REF cross-belt analyser calibration events")
def ref_analyser_calibrations():
    return ref_csv("analyser_calibrations").withColumn("calibrated_at", F.to_timestamp("calibrated_at"))


# ---------------------------------------------------------------------------- silver
@dp.materialized_view(comment="SILVER haul cycles: typed, de-duplicated on cycle_id, invalid payloads dropped")
@dp.expect_or_drop("valid_payload", "payload_t BETWEEN 150 AND 320")
@dp.expect_or_drop("valid_bm_fe", "bm_fe BETWEEN 45 AND 68")
@dp.expect("known_stockpile", "destination_stockpile IS NOT NULL")
def silver_haul_cycles():
    return (spark.read.table("bronze_haul_cycles")
            .withColumn("dump_ts", F.to_timestamp("dump_ts"))
            .dropDuplicates(["cycle_id"])
            .drop("_rescued_data"))


@dp.materialized_view(comment="SILVER trains: one row per departed train")
@dp.expect_or_fail("train_id_present", "train_id IS NOT NULL")
@dp.expect("plausible_tonnage", "wet_tonnes BETWEEN 25000 AND 40000")
def silver_trains():
    return (spark.read.table("bronze_train_consists")
            .select("train_id", "hub_code", "tlo_id", "product_code",
                    F.to_timestamp("load_start").alias("load_start"),
                    F.to_timestamp("depart_ts").alias("depart_ts"),
                    "wagons", F.col("wet_tonnes").cast("double").alias("wet_tonnes"),
                    F.col("moisture_pct").cast("double").alias("moisture_pct"))
            .dropDuplicates(["train_id"]))


@dp.materialized_view(comment="SILVER train reclaim mix: tonnes reclaimed from each stockpile per train")
def silver_train_reclaim():
    return (spark.read.table("bronze_train_consists")
            .select("train_id", "hub_code", F.to_timestamp("load_start").alias("load_start"),
                    F.explode("reclaim").alias("r"))
            .select("train_id", "hub_code", "load_start", F.col("r.stockpile_id").alias("stockpile_id"),
                    F.col("r.wet_tonnes").cast("double").alias("wet_tonnes")))


@dp.materialized_view(comment="SILVER planned trains for the current shift")
def silver_train_plan():
    return (spark.read.table("bronze_train_plan")
            .withColumn("planned_load_start", F.to_timestamp("planned_load_start"))
            .drop("_rescued_data", "_source_file", "_ingested_at")
            .dropDuplicates(["planned_train_id"]))


@dp.materialized_view(comment="SILVER cross-belt analyser readings: instrument FAULT rows and out-of-range values dropped")
@dp.expect_or_drop("analyser_ok", "status = 'OK'")
@dp.expect_or_drop("fe_in_range", "fe BETWEEN 45 AND 68")
def silver_xba_readings():
    return (spark.read.table("bronze_xba_readings")
            .withColumn("reading_ts", F.to_timestamp("reading_ts"))
            .drop("_rescued_data"))


@dp.materialized_view(comment="SILVER lab assays pivoted to one row per train composite; closure QA/QC failures dropped")
@dp.expect_or_drop("fe_closure", "fe BETWEEN 50 AND 66")
@dp.expect_or_drop("sio2_closure", "sio2 BETWEEN 1 AND 12")
@dp.expect("turnaround_under_72h", "lab_turnaround_h <= 72")
def silver_lab_assays():
    a = spark.read.table("bronze_lims_assays")
    ex = a.select("sample_id", "train_id", "lab", F.to_timestamp("received_ts").alias("received_ts"),
                  F.to_timestamp("result_ts").alias("result_ts"), F.explode("analytes").alias("an"))
    piv = (ex.groupBy("sample_id", "train_id", "lab", "received_ts", "result_ts")
           .pivot("an.code", ["FE", "SIO2", "AL2O3", "P", "LOI"]).agg(F.first("an.value")))
    for e in EL:
        piv = piv.withColumnRenamed(e.upper(), e)
    trains = spark.read.table("silver_trains").select("train_id", "depart_ts")
    return (piv.join(trains, "train_id", "left")
            .withColumn("lab_turnaround_h", (F.unix_timestamp("result_ts") - F.unix_timestamp("depart_ts")) / 3600)
            .drop("depart_ts"))


@dp.materialized_view(comment="SILVER vessel cargo composition: one row per (vessel, train)")
def silver_vessel_trains():
    return (spark.read.table("bronze_vessel_cargoes")
            .select("vessel_id", "vessel_name", "product_code", "berth", "destination",
                    F.to_timestamp("load_complete_ts").alias("load_complete_ts"),
                    F.explode("cargo_trains").alias("train_id")))


# ------------------------------------------------------------------------------ gold
def bm_window_grades(anchor_df, anchor_ts_col, key_cols):
    """Block-model grade of each stockpile over the 3 days before an anchor timestamp."""
    h = spark.read.table("silver_haul_cycles").select(
        F.col("destination_stockpile").alias("stockpile_id"), "dump_ts", "payload_t", *[f"bm_{e}" for e in EL])
    j = anchor_df.join(h, "stockpile_id").where(
        (F.col("dump_ts") < F.col(anchor_ts_col)) &
        (F.col("dump_ts") >= F.col(anchor_ts_col) - F.expr("INTERVAL 3 DAYS")))
    return j.groupBy(*key_cols).agg(
        *[(F.sum(F.col(f"bm_{e}") * F.col("payload_t")) / F.sum("payload_t")).alias(f"bm_{e}") for e in EL],
        F.sum("payload_t").alias("bm_window_t"))


@dp.materialized_view(comment="GOLD per-train ML features: analyser stats, reclaim-weighted block-model grade, "
                              "hub bias known at departure (no leakage), lab target where returned")
def train_features():
    trains = spark.read.table("silver_trains")
    rec = spark.read.table("silver_train_reclaim")
    sp_bm = bm_window_grades(rec.select("train_id", "stockpile_id", "load_start"), "load_start",
                             ["train_id", "stockpile_id"])
    rec_bm = rec.join(sp_bm, ["train_id", "stockpile_id"], "left")
    bm = rec_bm.groupBy("train_id").agg(
        *[(F.sum(F.col(f"bm_{e}") * F.col("wet_tonnes")) / F.sum("wet_tonnes")).alias(f"bm_{e}") for e in EL],
        F.count("*").alias("n_stockpiles"),
        F.max(F.col("wet_tonnes") / F.lit(34500)).alias("max_stockpile_share"))
    xba = spark.read.table("silver_xba_readings").groupBy("train_id").agg(
        *[F.avg(e).alias(f"xba_{e}") for e in EL], F.stddev("fe").alias("xba_fe_std"),
        F.count("*").alias("xba_n"))
    lab = spark.read.table("silver_lab_assays").select(
        "train_id", "result_ts", *[F.col(e).alias(f"lab_{e}") for e in EL])
    cal = spark.read.table("ref_analyser_calibrations")
    base = (trains.join(bm, "train_id", "left").join(xba, "train_id", "left").join(lab, "train_id", "left"))
    # days since last analyser calibration
    c = base.select("train_id", "tlo_id", "load_start").join(cal, "tlo_id", "left") \
        .where(F.col("calibrated_at") <= F.col("load_start")) \
        .groupBy("train_id").agg(F.max("calibrated_at").alias("last_cal"))
    base = base.join(c, "train_id", "left").withColumn(
        "days_since_cal", F.coalesce(F.datediff("load_start", "last_cal"), F.lit(30))).drop("last_cal")
    # hub bias known at departure: mean(lab - analyser) and mean(lab - block model) over trains
    # whose lab result had RETURNED before this train departed (last 14 days)
    hist = base.where(F.col("lab_fe").isNotNull()).select(
        F.col("hub_code").alias("h_hub"), F.col("result_ts").alias("h_res"), F.col("depart_ts").alias("h_dep"),
        *[(F.col(f"lab_{e}") - F.col(f"xba_{e}")).alias(f"d_xba_{e}") for e in EL],
        *[(F.col(f"lab_{e}") - F.col(f"bm_{e}")).alias(f"d_bm_{e}") for e in EL])
    k = base.select("train_id", "hub_code", "depart_ts").join(
        hist, (F.col("h_hub") == F.col("hub_code")) & (F.col("h_res") < F.col("depart_ts")) &
              (F.col("h_dep") >= F.col("depart_ts") - F.expr("INTERVAL 14 DAYS")), "left")
    bias = k.groupBy("train_id").agg(
        *[F.avg(f"d_xba_{e}").alias(f"hub_xba_bias_{e}") for e in EL],
        *[F.avg(f"d_bm_{e}").alias(f"hub_bm_bias_{e}") for e in EL],
        F.count("h_res").alias("hub_bias_n"))
    received = spark.read.table("bronze_lims_assays").select("train_id").distinct().withColumn("_lab_received", F.lit(True))
    return (base.join(bias, "train_id", "left").join(received, "train_id", "left")
            .withColumn("has_lab", F.col("lab_fe").isNotNull())
            .withColumn("lab_status", F.when(F.col("lab_fe").isNotNull(), "RETURNED")
                        .when(F.col("_lab_received"), "QAQC_REJECTED").otherwise("AWAITING_LAB"))
            .drop("_lab_received")
            .withColumn("hour_of_day", F.hour("load_start")))


@dp.materialized_view(comment="GOLD current state of every ROM stockpile: tonnes on hand, last-3-day block-model grade, "
                              "lab-reconciled grade (block model corrected by the hub's recent lab bias)",
                      table_properties={"delta.enableChangeDataFeed": "true"})
def stockpile_grade_current():
    sp = spark.read.table("ref_stockpiles")
    now = F.to_timestamp(F.lit(AS_OF))
    anchor = sp.select("stockpile_id").withColumn("as_of", now)
    bm = bm_window_grades(anchor, "as_of", ["stockpile_id"])
    hauls = spark.read.table("silver_haul_cycles").groupBy(F.col("destination_stockpile").alias("stockpile_id")) \
        .agg(F.sum("payload_t").alias("delivered_t"), F.max("dump_ts").alias("last_delivery_ts"))
    recl = spark.read.table("silver_train_reclaim").groupBy("stockpile_id").agg(
        F.sum("wet_tonnes").alias("reclaimed_t"), F.max("load_start").alias("last_reclaim_ts"))
    tf = spark.read.table("train_features").where("has_lab AND depart_ts >= " +
                                                   f"timestamp'{AS_OF}' - INTERVAL 14 DAYS")
    hub_bias = tf.groupBy("hub_code").agg(*[F.avg(F.col(f"lab_{e}") - F.col(f"bm_{e}")).alias(f"bias_{e}") for e in EL],
                                          F.count("*").alias("recon_trains"))
    out = (sp.join(bm, "stockpile_id", "left").join(hauls, "stockpile_id", "left")
           .join(recl, "stockpile_id", "left").join(hub_bias, "hub_code", "left")
           .withColumn("tonnes_on_hand", F.greatest(F.lit(0.0), F.col("opening_balance_t") + F.coalesce("delivered_t", F.lit(0.0))
                                                    - F.coalesce("reclaimed_t", F.lit(0.0)))))
    for e in EL:
        out = out.withColumn(f"recon_{e}", F.col(f"bm_{e}") + F.coalesce(F.col(f"bias_{e}"), F.lit(0.0)))
    return out.select("stockpile_id", "hub_code", "grade_class", "product_code", "capacity_t", "opening_balance_t", "tonnes_on_hand",
                      *[F.round(f"bm_{e}", 4).alias(f"bm_{e}") for e in EL],
                      *[F.round(f"recon_{e}", 4).alias(f"recon_{e}") for e in EL],
                      F.round("bias_fe", 3).alias("hub_bm_bias_fe"), "recon_trains",
                      "last_delivery_ts", "last_reclaim_ts", now.alias("as_of_ts"))


TRAIN_PERF_SCHEMA = """
  train_id STRING, hub_code STRING, product_code STRING, load_start TIMESTAMP, depart_ts TIMESTAMP,
  depart_date DATE, wet_tonnes DOUBLE, dry_tonnes DOUBLE, moisture_pct DOUBLE,
  bm_fe DOUBLE, xba_fe DOUBLE, lab_fe DOUBLE, lab_sio2 DOUBLE, lab_al2o3 DOUBLE, lab_p DOUBLE, lab_loi DOUBLE,
  fe_typ DOUBLE, fe_min DOUBLE, sio2_max DOUBLE, al2o3_max DOUBLE, p_max DOUBLE,
  fe_vs_typical DOUBLE, below_typical_fe BOOLEAN, below_min_fe BOOLEAN, limit_breach BOOLEAN,
  bm_fe_error DOUBLE, xba_fe_error DOUBLE, lab_status STRING
"""


@dp.materialized_view(comment="GOLD train performance vs product spec (row filter: grade controllers see their own hub only)",
                      schema=TRAIN_PERF_SCHEMA,
                      row_filter=f"ROW FILTER {SCHEMA}.hub_row_filter ON (hub_code)")
def train_performance():
    tf = spark.read.table("train_features")
    spec = spark.read.table("ref_product_specs")
    return (tf.join(spec, "product_code", "left").select(
        "train_id", "hub_code", "product_code", "load_start", "depart_ts",
        F.to_date("depart_ts").alias("depart_date"), "wet_tonnes",
        (F.col("wet_tonnes") * (1 - F.col("moisture_pct") / 100)).alias("dry_tonnes"), "moisture_pct",
        "bm_fe", "xba_fe", "lab_fe", "lab_sio2", "lab_al2o3", "lab_p", "lab_loi",
        F.col("fe_typ").cast("double").alias("fe_typ"), F.col("fe_min").cast("double").alias("fe_min"),
        F.col("sio2_max").cast("double").alias("sio2_max"), F.col("al2o3_max").cast("double").alias("al2o3_max"),
        F.col("p_max").cast("double").alias("p_max"),
        (F.col("lab_fe") - F.col("fe_typ")).alias("fe_vs_typical"),
        (F.col("lab_fe") < F.col("fe_typ")).alias("below_typical_fe"),
        (F.col("lab_fe") < F.col("fe_min")).alias("below_min_fe"),
        ((F.col("lab_fe") < F.col("fe_min")) | (F.col("lab_sio2") > F.col("sio2_max")) |
         (F.col("lab_al2o3") > F.col("al2o3_max")) | (F.col("lab_p") > F.col("p_max"))).alias("limit_breach"),
        (F.col("bm_fe") - F.col("lab_fe")).alias("bm_fe_error"),
        (F.col("xba_fe") - F.col("lab_fe")).alias("xba_fe_error"),
        "lab_status"))


VESSEL_SCHEMA = f"""
  vessel_id STRING, vessel_name STRING, product_code STRING, destination STRING, berth STRING,
  load_complete_ts TIMESTAMP, load_month DATE, n_trains BIGINT, wet_tonnes DOUBLE, dry_tonnes DOUBLE,
  fe DOUBLE, sio2 DOUBLE, al2o3 DOUBLE, p DOUBLE, loi DOUBLE, fe_typ DOUBLE, fe_min DOUBLE,
  off_spec_trains BIGINT, below_typical BOOLEAN, limit_breach BOOLEAN,
  penalty_usd_per_dmt DOUBLE MASK {SCHEMA}.commercial_mask,
  penalty_usd DOUBLE MASK {SCHEMA}.commercial_mask
"""


@dp.materialized_view(comment="GOLD vessel cargo grade vs spec and illustrative grade penalty "
                              "(column mask: penalty USD visible to commercial roles only)",
                      schema=VESSEL_SCHEMA)
def vessel_compliance():
    vt = spark.read.table("silver_vessel_trains")
    tf = spark.read.table("train_features").select(
        "train_id", "wet_tonnes", "moisture_pct", *[f"lab_{e}" for e in EL])
    spec = spark.read.table("ref_product_specs")
    pen_row = (spark.read.table("ref_penalty_schedule")
               .withColumn("k", F.concat_ws("_", "element", "basis"))
               .withColumn("rate", F.col("usd_per_dmt_per_unit") / F.col("unit_pct"))
               .groupBy().pivot("k", ["fe_below_typical", "fe_below_min", "sio2_above_max",
                                      "al2o3_above_max", "p_above_max"]).agg(F.first("rate")))
    pen = {c: F.col(f"pen_{c}") for c in pen_row.columns}
    pen_row = pen_row.select(*[F.col(c).alias(f"pen_{c}") for c in pen_row.columns])
    j = vt.join(tf, "train_id", "left").withColumn("dry_t", F.col("wet_tonnes") * (1 - F.col("moisture_pct") / 100))
    j = j.join(spec.select("product_code", "fe_typ", "fe_min"), "product_code", "left") \
        .withColumn("train_below_typ", (F.col("lab_fe") < F.col("fe_typ")).cast("int"))
    v = j.groupBy("vessel_id", "vessel_name", "product_code", "destination", "berth", "load_complete_ts").agg(
        F.count("*").alias("n_trains"), F.sum("wet_tonnes").alias("wet_tonnes"), F.sum("dry_t").alias("dry_tonnes"),
        *[(F.sum(F.col(f"lab_{e}") * F.col("dry_t")) /
           F.sum(F.when(F.col(f"lab_{e}").isNotNull(), F.col("dry_t")))).alias(e) for e in EL],
        F.sum("train_below_typ").alias("off_spec_trains"))
    v = v.join(spec, "product_code", "left").crossJoin(pen_row)
    ppd = (F.greatest(F.lit(0.0), F.col("fe_typ") - F.col("fe")) * pen["fe_below_typical"]
           + F.greatest(F.lit(0.0), F.col("fe_min") - F.col("fe")) * pen["fe_below_min"]
           + F.greatest(F.lit(0.0), F.col("sio2") - F.col("sio2_max")) * pen["sio2_above_max"]
           + F.greatest(F.lit(0.0), F.col("al2o3") - F.col("al2o3_max")) * pen["al2o3_above_max"]
           + F.greatest(F.lit(0.0), F.col("p") - F.col("p_max")) * pen["p_above_max"])
    return v.select(
        "vessel_id", "vessel_name", "product_code", "destination", "berth", "load_complete_ts",
        F.trunc("load_complete_ts", "month").alias("load_month"), "n_trains", "wet_tonnes", "dry_tonnes",
        *[F.round(e, 4).alias(e) for e in EL],
        F.col("fe_typ").cast("double").alias("fe_typ"), F.col("fe_min").cast("double").alias("fe_min"),
        F.col("off_spec_trains").cast("bigint").alias("off_spec_trains"),
        (F.col("fe") < F.col("fe_typ")).alias("below_typical"),
        ((F.col("fe") < F.col("fe_min")) | (F.col("sio2") > F.col("sio2_max")) |
         (F.col("al2o3") > F.col("al2o3_max"))).alias("limit_breach"),
        F.round(ppd, 4).alias("penalty_usd_per_dmt"),
        F.round(ppd * F.col("dry_tonnes"), 2).alias("penalty_usd"))
