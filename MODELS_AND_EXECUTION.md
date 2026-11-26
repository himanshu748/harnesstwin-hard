# GMI Cloud models and execution

The authenticated GMI `GET /v1/models` call returned 71 IDs on 26 November 2026. The runner uses the IDs exactly as returned.

| Role | Exact serving ID | Execution |
| --- | --- | --- |
| Flash pilot | `zai-org/GLM-5.3-Flash` | Two matched HARD-51 tasks, one attempt per harness |
| Main run | `zai-org/GLM-5.3` | Ten preregistered HARD-51 tasks, first trial |
| Cross-model candidate | `deepseek-ai/DeepSeek-V4.1-Flash` | Inventory verified |
| Cross-model candidate, dated | `deepseek-ai/deepseek-v4.1-flash-1017` | Inventory verified |
| Stress-test candidate | `moonshotai/kimi-k3` | Inventory verified |

The DeepSeek billing probe uses `deepseek-ai/DeepSeek-V4-Flash`. It supports the Models.dev pricing PR and does not measure DeepSeek V4.1 Flash benchmark performance.

The hosted environment is a standard public `ubuntu-24.04` x64 GitHub Actions runner. Each agent and verifier starts fresh with the official OCI image resolved to its digest. The agent image adds pinned versions of Pi, OpenCode and mini-SWE-agent. The relay resides on the host, and only it can reach GMI. The verifier receives only the candidate patch and the official verifier files.

[Authenticated inventory](evidence/inventory.json) · [Execution and reproduction](README.md) · [Technical report](REPORT.md)
