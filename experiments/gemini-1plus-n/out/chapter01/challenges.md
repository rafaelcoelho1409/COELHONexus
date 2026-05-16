1. Your team is building a simple chatbot. Use LiteLLM's Python SDK to make a call to `gpt-3.5-turbo` with a user message "Tell me a fun fact about space." Extract and print the generated content.

2. You've successfully used `gpt-3.5-turbo`, but now your manager wants to test `claude-2` for a specific prompt: "Explain the concept of quantum entanglement in simple terms." Modify your LiteLLM SDK code to switch to `claude-2` (assuming `ANTHROPIC_API_KEY` is set) and get the response, demonstrating the ease of model switching.

3. Regardless of the LLM provider, your application needs to display the generated text in a consistent UI element. Write a Python snippet using LiteLLM to call any model (e.g., `gpt-4`) with a simple prompt, and then reliably extract the main text content using the LiteLLM's consistent output path, ensuring your UI code doesn't need to change per model.

4. You are a solo developer working on a personal project that uses a few different LLMs for creative writing prompts. You want to integrate these LLM calls directly into your Python script. Which LiteLLM usage method (SDK or Proxy) would you choose and why, considering your project's scope and your role?

5. Your company has multiple product teams, each building features that rely on various LLMs. The ML Platform team needs to monitor overall LLM spend, enforce usage policies, and provide a single, reliable endpoint for all teams. Which LiteLLM usage method would be most appropriate for the ML Platform team to implement and why?

6. You're developing a function that accepts a list of `messages` in the OpenAI chat format. Explain how LiteLLM allows this single function to seamlessly interact with both OpenAI's `gpt-4` and Cohere's `command-nightly` without requiring any changes to the `messages` structure itself.

7. You're setting up your development environment. Describe the standard way LiteLLM expects you to provide API keys for different providers (e.g., OpenAI, Anthropic) before making `completion()` calls, and briefly explain why this method is preferred over hardcoding.