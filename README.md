# Customer Support Agent — multi-agent ADK demo

A working customer support agent built with the [Google Agent Development Kit (ADK)](https://adk.dev/)
and deployable to Vertex AI Agent Runtime. One coordinator routes each question to a
specialist, then rewrites the answer for the user.

This repo is meant to be read. Every piece — routing, tool-level access control,
memory, evaluation, deployment — is small enough to follow in one sitting.

---

## What it does

```
                     ┌──────────────────────────────────────────┐
User ───────────────►│  root_agent  (coordinator)               │
                     │  gemini-3.1-pro-preview                  │
                     │                                          │
                     │  routes ──┬─► BillingSpecialist          │
                     │           │     └─ lookup_invoice        │
                     │           ├─► ShippingSpecialist         │
                     │           │     └─ get_order_status      │
                     │           │     └─ 🚫 deny_billing_tools │
                     │           └─► RefundsSpecialist          │
                     │                                          │
                     │  own tools: get_weather, get_current_time│
                     │  PreloadMemoryTool (past conversations)  │
                     └──────────────────────────────────────────┘
                                     │
                                     ▼
                        one synthesized, warm answer
```

Ask *"Where is my order ORD-8871?"* → coordinator calls `ShippingSpecialist` →
specialist calls `get_order_status` → coordinator rewrites the raw facts into a
friendly two-to-four sentence reply and names the next step.

---

## The five ideas worth learning here

### 1. `AgentTool`, not `sub_agents`

The specialists are attached as `AgentTool(agent=...)`, not as `sub_agents`.

With `sub_agents`, ADK *transfers* control — the specialist replies to the user
directly and the coordinator has nothing left to do. With `AgentTool`, the
specialist's answer comes back as a tool result, so the coordinator can rewrite
it in one consistent voice. See the `# ponytail:` comment on `root_agent` in
`app/agent.py`.

### 2. Two models, on purpose

| Role | Model | Why |
|---|---|---|
| Coordinator | `gemini-3.1-pro-preview` | routing + synthesis need judgement |
| Specialists | `gemini-3.7-flash` | narrow job, one tool, fast and cheap |

Set at the top of `app/agent.py` as `COORDINATOR_MODEL` / `WORKER_MODEL`.

### 3. Access control in code, not in the prompt

`ShippingSpecialist` must never touch billing data. Two layers enforce that:

```python
BILLING_TOOLS = [lookup_invoice]
BILLING_TOOL_NAMES = frozenset(t.__name__ for t in BILLING_TOOLS)

def deny_billing_tools(tool, args, tool_context) -> dict | None:
    if tool.name in BILLING_TOOL_NAMES:
        return {"error": f"Access denied: {tool.name} is a billing tool ..."}
    return None   # None = allow the call
```

- **Toolset**: the billing tool is simply not in the shipping agent's tool list.
- **Callback**: `before_tool_callback=deny_billing_tools` blocks it at call time
  anyway. A returned dict short-circuits the tool; `None` lets it through.

Why both? An instruction ("don't use billing tools") is a *suggestion* a prompt
injection can talk the model out of. A callback is code — it cannot be argued
with. `tests/unit/test_tool_policy.py` proves all of it.

Add a new billing tool to `BILLING_TOOLS` and every guarded agent inherits the
restriction; no other file changes.

### 4. Memory across sessions

- `PreloadMemoryTool()` injects a `PAST_CONVERSATIONS` block into the request of
  the agent it is attached to. Note the coordinator has **its own** copy — memory
  does not flow down to it from elsewhere.
- `after_agent_callback=persist_turn_to_memory` writes the finished turn (user
  message, tool calls, final answer) to memory in one go.
- Backend is picked in `app/app_utils/services.py`: Vertex AI Memory Bank when
  `GOOGLE_CLOUD_AGENT_ENGINE_ID` is set, in-memory otherwise — so a local run
  exercises the same code path with no cloud setup.

### 5. One agent, one identity

The whole thing — coordinator plus three specialists — deploys as **one** agent
with **one** service identity. The specialists are objects in one process, not
separate deployments. So:

| Layer | Mechanism | Controls |
|---|---|---|
| Inside the process | `deny_billing_tools` | which sub-agent may call which tool |
| The cloud boundary | Agent Identity / IAM | what the whole agent may touch in GCP |

---

## Project structure

```
customer-support-agent/
├── app/
│   ├── agent.py                  # ← start here: agents, tools, policy, memory
│   ├── fast_api_app.py           # FastAPI server (ADK routes + A2A)
│   └── app_utils/
│       ├── services.py           # session / artifact / memory service wiring
│       ├── a2a.py                # Agent2Agent protocol endpoints
│       └── reasoning_engine_adapter.py
├── tests/
│   ├── unit/test_tool_policy.py  # the billing-access policy, proven
│   ├── integration/              # agent stream + server end-to-end
│   └── eval/                     # datasets + LLM-as-judge config
├── deployment/terraform/         # infra (single-project layout)
├── GEMINI.md                     # AI-assisted development guide
└── pyproject.toml
```

---

## Requirements

- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — package manager (add packages with `uv add <pkg>`; never plain `pip`)
- **agents-cli** — `uv tool install google-agents-cli`
- **[Google Cloud SDK](https://cloud.google.com/sdk/docs/install)** — for GCP auth and deployment
- Python 3.11–3.13

---

## Quick start

```bash
# 1. install the CLI and its skills
uvx google-agents-cli setup

# 2. install project dependencies
agents-cli install

# 3. configure your environment
cp .env.example .env
#    edit .env: set GOOGLE_CLOUD_PROJECT to your project id
gcloud auth application-default login

# 4. run it locally, with a web UI that reloads on save
agents-cli playground
```

Prefer Google AI Studio over Vertex AI? In `.env`, comment out the three
`GOOGLE_*` lines and set `GEMINI_API_KEY=...` instead.

Try these in the playground:

| Ask | Expected route |
|---|---|
| `Can you tell me the status of invoice INV-1042?` | BillingSpecialist |
| `Where is my order ORD-8871?` | ShippingSpecialist |
| `How long does a refund take?` | RefundsSpecialist |
| `What's the weather in SF?` | coordinator answers directly |

The invoice and order tools return **fixture data** (see the `# ponytail:`
comments) — swap them for your real billing/fulfilment API clients.

---

## Commands

| Command | What it does |
|---|---|
| `agents-cli install` | Install dependencies with uv |
| `agents-cli playground` | Local dev server + web UI |
| `agents-cli lint` | ruff + ty + codespell |
| `uv run pytest tests/unit tests/integration` | Unit and integration tests |
| `agents-cli eval` | Generate, grade and analyze evals |
| `agents-cli deploy` | Deploy to Vertex AI Agent Runtime |
| `agents-cli publish gemini-enterprise` | Register the deployed agent |

ADK's own CLI is available too: `uv run adk ...`

---

## Evaluation

`tests/eval/` holds the eval datasets and the grading config.

- `datasets/customer-support-dataset.json` — one case per routing path.
- `eval_config.yaml` — which metrics to run.
- `response_quality.py` — a local LLM-as-judge scoring 1–5 on accuracy,
  relevance and clarity, returning a structured `{score, explanation}` verdict.
- `agent_turn_count` — an inline metric, an example of how cheap a custom metric
  can be.

```bash
agents-cli eval          # see `agents-cli eval --help` for subcommands
```

Results land in `artifacts/grade_results/` as HTML + JSON (gitignored).

---

## Deployment

```bash
gcloud config set project <your-project-id>
agents-cli deploy
```

For Terraform infrastructure, copy the vars template first:

```bash
cp deployment/terraform/single-project/vars/env.tfvars.example \
   deployment/terraform/single-project/vars/env.tfvars
# edit project_id, then:
agents-cli infra cicd     # sets up CI/CD + infrastructure in one command
```

`env.tfvars` and `.env` are gitignored — real project ids and keys stay local.

Telemetry is built in: traces to Cloud Trace, logs to Cloud Logging, completions
to BigQuery.

---

## A2A (Agent2Agent)

The FastAPI app serves an [A2A protocol](https://a2a-protocol.org/) agent card
and JSON-RPC endpoint alongside the ADK routes, so other agents can call this one.
Test it with the [A2A Inspector](https://github.com/a2aproject/a2a-inspector).

---

## Where to change what

| You want to… | Edit |
|---|---|
| Add a specialist | `_worker(...)` + one `AgentTool` in `root_agent.tools` |
| Add a tool | a plain function with a docstring — the docstring *is* the tool spec |
| Change routing rules | `COORDINATOR_INSTRUCTION` |
| Restrict a tool | add it to `BILLING_TOOLS`, or write a new `before_tool_callback` |
| Swap models | `COORDINATOR_MODEL` / `WORKER_MODEL` |
| Change memory backend | `app/app_utils/services.py` |

Scaffolded with `agents-cli` 1.4.2. Apache 2.0 licensed source headers.
