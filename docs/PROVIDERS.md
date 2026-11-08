# Model providers

Workbench supports Gemini, OpenAI (ChatGPT models), Anthropic Claude and local Ollama. Ollama is the startup default. The toolbar and Settings provider selectors stay synchronized for the next run. Analysis, full-corpus jobs, witness work product, question generation and document-context editing use that selection; running jobs retain their recorded provider/model. There is no automatic provider failover. Embeddings continue to use local Ollama independently of the generation provider.

## Add API keys now

1. Refresh the Workbench browser and open **Settings**.
2. In **OpenAI · ChatGPT models** or **Anthropic · Claude**, paste the provider's API key and click **Save API key**.
3. Click **Load available models**, choose a model from the input's suggestions or enter its model ID, then click **Save model**.
4. Click **Test connection**. This sends a short fictional example to the saved model, never workspace evidence. Model catalogs can include models that do not support the required structured output; the connection test checks this.
5. Select the provider under **Provider for next run** or in the main toolbar, then start the intended workflow.

Keys are saved to the existing server-side `.env` file, excluded from Git. Settings returns only whether a key is configured. Password fields clear after a save attempt; keys are never prefilled or returned to the browser. Saving a key or model does not contact the provider or start a corpus job. Changes take effect immediately. The optional manual privacy-review gate applies consistently to every cloud provider; automatic masking remains enabled. Do not paste keys into chat, evidence, or attorney instructions.

Configuration variables: `OPENAI_API_KEY`, `OPENAI_MODEL` (initially `gpt-4.1`), `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (initially `claude-sonnet-5-5`). Account availability varies; use the model catalog and connection test for the configured account.

## Implementation

OpenAI uses the [Responses API structured-output format](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses), with `store: false`. Claude uses the [Messages API structured-output format](https://platform.claude.com/docs/en/build-with-claude/structured-outputs). Provider schemas require explicit object properties; unsupported constraints are retained in descriptions and checked against the original Pydantic model after generation. Truncated, refused, empty or invalid responses are rejected. Transient failures have bounded retries; authentication and request failures return application-authored diagnostics without raw provider content.

All cloud providers share these endpoints:

- `POST /api/settings/providers/{provider}/key` with `api_key`
- `POST /api/settings/providers/{provider}/model` with `model`
- `GET /api/settings/providers/{provider}/models`
- `POST /api/settings/providers/{provider}/test`

Provider IDs are `gemini`, `openai`, `anthropic`; existing Gemini-specific routes remain supported. The connection tests and mocked regression tests verify integration behavior, not legal accuracy. Live OpenAI/Claude generation requires a valid key and a compatible account/model.
