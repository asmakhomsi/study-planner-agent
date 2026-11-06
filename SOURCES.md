# Free LLM sources I compared

The brief's shared DeepSeek credits ran out, so I looked for free access myself. The starter list was Groq,
Cerebras, Google AI Studio, NVIDIA NIM and OpenRouter; **DeepSeek and Mistral are not on it**.

"Tool calling" is the result of `python smoke_test.py` (`smoke/smoke_results.json`): one prompt, "What is 17 + 25?
Use the tool.", with one `add(a, b)` tool. PASS means a real `tool_calls` entry `add(17, 25)`, not text that looks
like one. Free limits change often: the numbers marked *reported* come from third-party pages and were not all
confirmed in my consoles; where my own runs show a limit, I say so ("Seen").

| Provider | Sign-up at | Free limits (as seen, with date) | Card needed | Phone needed | Data policy (trains on prompts?) | Tool calling (smoke test, 2026-11-06) | Verdict for this project |
|---|---|---|---|---|---|---|---|
| **OpenRouter** (`nvidia/nemotron-3-ultra-550b-a55b:free`) | openrouter.ai | *Reported:* 20 requests/min and 50 requests/day on `:free` models with under 10 credits bought; the day resets at midnight UTC. My console shows no daily number (2026-11-08). **Seen:** on 2026-11-06 my batch got `429 Rate limit exceeded: free-models-per-day` after 52 successful requests (48 in runs + 4 smoke tests), which matches about 50/day. On 2026-11-08 the 429 reply itself said `X-RateLimit-Limit: 50` | No | No | Depends on the host behind each free model: OpenRouter publishes each provider's policy, and an account setting can block hosts that train. This model is hosted by NVIDIA, and OpenRouter's endpoint list shows no data policy for it (checked 2026-11-08). A third-party review (Oct 2026) found about half of the free models' hosts allow training on prompts | PASS, `add(17, 25)`, 2.64 s, 391 tokens | **Model A.** 50 requests/day is about 6 runs/day, so 15 runs take about 3 days. Chosen after DeepSeek failed |
| **Google AI Studio** (`gemini-3.5-flash-lite`) | aistudio.google.com | *Reported:* about 500 requests/day and 10 requests/min. In my own earlier runs, `gemini-3.5-flash` stopped at 20 requests/day (FIXLOG). Console limits not checked. **Seen:** all 15 comparison runs, 146 requests, on 2026-11-06 with no 429 at all | No | No | **Yes on the free tier:** Google's pricing page lists "used to improve our products" for free use (not for paid), and human reviewers may read free-tier prompts after they are disconnected from the account | PASS, `add(17, 25)`, 0.79 s, 88 tokens | **Model B.** Fast and enough requests for all 15 runs in one day. Paced at one call per 6.5 s |
| **Groq** (`openai/gpt-oss-120b`) | console.groq.com | *Reported:* 30 requests/min, 1,000 requests/day, **8,000 tokens/min**, 200,000 tokens/day. Console limits not checked | No | No | **No training** per Groq's terms (prompts used only to run the service). Its docs describe 30-day standard retention with a zero-retention option in Data Controls | PASS, `add(17, 25)`, 0.78 s, 186 tokens | Smoke-tested only. One request of ours reaches about 24K tokens, bigger than 8K tokens/min, so a full run can't fit |
| **DeepSeek** (`deepseek-v4-flash`) | platform.deepseek.com | Expected a 5M-token signup grant (30 days, no card). My console showed a balance of 0 and no grant | No | No | **Unclear for the API.** The privacy policy allows training on inputs (with an opt-out in some regions), and sources disagree on whether the API is exempt; data is stored in China | **FAIL: HTTP 402 Insufficient Balance.** The key was accepted (not 401), but the account had no balance | Not used. Was the planned model A |
| **NVIDIA NIM** | build.nvidia.com | Not reached | No | **Yes**: SMS verification; the code never arrived from Tunisia | - | Not tested (no key) | Not used. The same Nemotron family is reachable through OpenRouter |
| **Mistral** | console.mistral.ai | The free plan no longer activates API keys (Oct 2026) | No | No | - | Not tested (no key) | Not used |
| **Cerebras** | cloud.cerebras.ai | Trial credits need a card. *Reported:* a context cap of about 8K tokens on the free tier | **Yes** | - | - | Not tested | Not tried |

**Backup checked:** `cohere/north-mini-code:free` on OpenRouter (key `d`) also passed the smoke test (`add(17, 25)`,
1.28 s, 110 tokens). It stays a backup for model A.

**Data policy sources** (checked 2026-11-08; policies change, so these describe what the terms allow, not what
happened to my prompts): [OpenRouter provider logging](https://openrouter.ai/docs/guides/privacy/provider-logging),
[OpenRouter privacy and logging](https://openrouter.ai/docs/features/privacy-and-logging),
[OpenRouter free models review, TrueFoundry](https://www.truefoundry.com/blog/openrouter-free-models-whats-actually-free-and-what-it-costs-you),
[Gemini API billing](https://ai.google.dev/gemini-api/docs/billing),
[Groq: your data](https://console.groq.com/docs/your-data),
[DeepSeek API data policy investigation](https://cncsmonster.github.io/en/posts/deepseek-api-data-training-policy/).
My prompts contain only made-up study data, so none of this was a blocker.

**Why these two models.** They come from different families (NVIDIA Nemotron and Google Gemini), both make real
tool calls, and both are free without a card. Gemma (also on OpenRouter for free) was left out because it comes from
Google, the same family as Gemini.
