"""Ask the Genie Agent business questions via the Conversation API; write answers + SQL + results as markdown evidence.
Usage: python scripts/genie_conversations.py > evidence/07_genie_conversations.md
"""
import time
from databricks.sdk import WorkspaceClient

SPACE = "01f1d784cd381d9a847bfae03a44cfbf"
QUESTIONS = [
    "Which hub had the highest share of trains below typical Fe in October?",
    "What is the block model Fe bias and analyser Fe error by hub?",
    "How many trains are in flight right now and how many are predicted below typical Fe, by hub?",
    "Show monthly vessel grade penalty in USD by product",
    "Which stockpiles have the biggest gap between block model Fe and lab-reconciled Fe?",
    "What blend is recommended for the next Solomon Fortescue Blend train and why?",
]
w = WorkspaceClient(profile="fevm-serverless-stable-1s8h43")
print(f"# Genie Agent conversations (space `{SPACE}`)\n")
print(f"Asked as `{w.current_user.me().user_name}` via the Genie Conversation API on {time.strftime('%Y-%m-%d %H:%M %Z')}. "
      "Each answer is generated live: Genie writes SQL against the Unity Catalog metric views / gold tables; UC row filters and masks apply to the asker.\n")
for i, q in enumerate(QUESTIONS, 1):
    t0 = time.time()
    m = w.genie.start_conversation_and_wait(SPACE, q)
    print(f"## Q{i}. {q}\n\n_status {m.status.value if m.status else '?'} in {time.time() - t0:.0f}s_\n")
    for a in m.attachments or []:
        if a.text and a.text.content:
            print(f"**Answer:** {a.text.content}\n")
        if a.query:
            if a.query.description:
                print(f"_Interpretation:_ {a.query.description}\n")
            print("```sql\n" + a.query.query.strip() + "\n```\n")
            r = w.genie.get_message_attachment_query_result(SPACE, m.conversation_id, m.id, a.attachment_id).statement_response
            if r and r.manifest and r.result:
                cols = [c.name for c in r.manifest.schema.columns]
                print("| " + " | ".join(cols) + " |\n|" + "---|" * len(cols))
                for row in (r.result.data_array or [])[:15]:
                    print("| " + " | ".join("" if v is None else str(v)[:120] for v in row) + " |")
                print()
