# LLM-friendly Helpers and Integrations

This document outlines the conceptual details for integrating the Re-Searcher API with external tools like Obsidian and ChatGPT.

## Obsidian Plugin Sketch

An Obsidian plugin would allow users to seamlessly search their Re-Searcher library from within Obsidian and insert links to relevant documents.

### Features

*   **Search Command**: A new command (e.g., "Re-Searcher: Search") would open a modal search window.
*   **Search Modal**: The modal would contain a search bar. As the user types, it would call the `/api/search` endpoint of the Re-Searcher API.
*   **Result Picker**: The search results would be displayed in a list within the modal. The user could navigate the results with the keyboard.
*   **Insert Link**: On selecting a result, the plugin would insert a link into the current note. The link could be a `zotero://` link for Zotero items or a file path for local files.
*   **Settings**: A settings pane would allow the user to configure the Re-Searcher API endpoint URL and the API key.

### Implementation Details

*   **Language**: The plugin would be written in JavaScript/TypeScript, as is standard for Obsidian plugins.
*   **API Calls**: It would use the `fetch` API to communicate with the Re-Searcher API. The API key would be sent in the `X-API-Key` header.
*   **UI**: The search modal and settings pane would be built using the Obsidian API for UI components.

## ChatGPT Integration Pattern

Integrating with ChatGPT (or other large language models) would allow for powerful summarization and question-answering capabilities over the user's document library.

### Workflow

1.  **Function Calling Wrapper**: A custom function calling wrapper would be created for the LLM. This wrapper would know how to call the Re-Searcher API.
2.  **Environment Variables**: The Re-Searcher API endpoint and API key would be provided to the wrapper as environment variables.
3.  **User Query**: When a user asks a question, the LLM would determine if it needs to access the user's documents to answer it.
4.  **API Call**: If so, the LLM would use the function calling wrapper to call the `/api/summarise` endpoint of the Re-Searcher API with the user's query.
5.  **Summary and Citations**: The Re-Searcher API would return a summary of the most relevant documents, along with citations (e.g., Zotero links or file paths).
6.  **Response Generation**: The LLM would then use this summary and the citations to generate a comprehensive answer for the user.
7.  **Security (Optional)**: A lambda proxy could be placed between the LLM and the Re-Searcher API to scrub any private or sensitive information from the API responses before they are sent back to the LLM.
