# Re-Searcher

**Semantic Search For Researchers**

Re-Searcher is a tool designed to help researchers perform semantic and keyword searches across their local documents and Zotero library. It is built with a Python backend using FastAPI and a Streamlit frontend.

## Features

- **Semantic Search**: Find documents based on the meaning of your query, not just keywords.
- **Keyword Search**: Perform traditional keyword searches with advanced operators.
- **Hybrid Search**: Combine semantic and keyword search for the best of both worlds.
- **Zotero Integration**: Search your Zotero library, including metadata, notes, and attachments.
- **Multi-format Support**: Extracts text from PDF, DOCX, HTML, Markdown, and EPUB files.
- **Faceted Filtering**: Filter search results by tags, collections, and year.
- **API and UI**: A FastAPI backend provides a powerful API, and a Streamlit app provides a user-friendly interface.
- **LLM-friendly**: Includes an API endpoint for summarizing text, making it easy to integrate with large language models.
- **Ontology Expansion**: (Optional) Define your own conceptual framework in a YAML file; Re‑Searcher can expand your queries with related terms and synonyms defined in that ontology.

## Project Structure

- `src/`: Contains the core Python source code for the application, including text extraction, search logic, and the FastAPI.
- `ui/`: Contains the Streamlit user interface code.
- `docs/`: Contains the project documentation, built with MkDocs.
- `tests/`: Contains unit tests for the application.
- `TestFiles/`: Contains a small demo Zotero database and Storage folder tree for testing purposes. Also contains demo md and word files for testing.

## Getting Started

1.  **Install dependencies**:

    ```bash
    pip install -r requirements.txt
    ```

2.  **Set up your environment**:

    - Rename `.env.example` to `.env` and add a secret API key.
    - Update `config.example.yaml` with the paths to your input documents and Zotero data directory.  If you wish to enable ontology support, set the `ontology_file` field to point at a YAML file that defines your concepts (see `ontology.example.yaml` for a template).  Rename the file to `config.yaml`.

3.  **Build the search index**:

    ```bash
    python -m src.semantic_search
    ```

4.  **Run the API**:

    ```bash
    python -m src.api
    ```

5.  **Run the Streamlit UI**:
    ```bash
    streamlit run ui/semantic_app.py
    ```
