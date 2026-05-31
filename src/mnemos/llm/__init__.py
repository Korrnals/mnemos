"""LLM provider abstraction for Mnemos.

Submodules:
  base        — Provider interface (abstract base class)
  anthropic   — Anthropic Claude (primary recommended for synthesis)
  openai      — OpenAI GPT
  azure_openai — Azure OpenAI (uses openai SDK with azure config)
  ollama      — Local Ollama (privacy + offline)
  gemini      — Google Gemini

Provider selection order (recommended): Anthropic → Ollama → OpenAI → Azure → Gemini
Configured in ~/.mnemos/config.yaml or MNEMOS_LLM__PROVIDER env var.
"""
