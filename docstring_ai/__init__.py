"""
This module imports and defines constants related to the model configuration for processing tasks.

These constants include information on model names, token limits, embedding models,
API request limits, caching strategies, and database collections. They are designed to facilitate
the configuration of model interactions and storage mechanisms, ensuring that the application can 
be easily maintained and adapted as needed.

Constants:
    MODEL (str): 
        The name of the model to be used for processing tasks.
    MAX_TOKENS (int): 
        The maximum number of tokens allowed in a single request to the model.
    EMBEDDING_MODEL (str): 
        The model used for embedding text data into numerical vectors.
    MAX_RETRIES (int): 
        The maximum number of retry attempts for API requests to handle transient errors.
    RETRY_BACKOFF (int): 
        The time (in seconds) to wait before retrying a failed API request to avoid immediate retries.
    CHROMA_COLLECTION_NAME (str): 
        The name of the ChromaDB collection used to store context data relevant to processing.
    CACHE_FILE_NAME (str): 
        The name of the file used for caching purposes to optimize the retrieval of stored results.
    CONTEXT_SUMMARY_PATH (str): 
        The file path for storing context summaries during processing tasks.
"""

from .lib.config import (
    MODEL,  # str: The name of the model to be used for processing tasks.
    MAX_TOKENS,  # int: The maximum number of tokens allowed in a single request to the model.
    EMBEDDING_MODEL,  # str: The model used for embedding text data into numerical vectors.
    MAX_RETRIES,  # int: The maximum number of retry attempts for API requests to handle transient errors.
    RETRY_BACKOFF,  # int: The time (in seconds) to wait before retrying a failed API request.
    CHROMA_COLLECTION_NAME,  # str: The name of the ChromaDB collection used to store context data.
    CACHE_FILE_NAME,  # str: The name of the file used for caching purposes to optimize retrieval.
    CONTEXT_SUMMARY_PATH , # str: The file path for storing context summaries during processing tasks.,
    DATA_PATH
)
