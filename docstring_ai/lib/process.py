# Docstring generated by docstring-ai : http://github.com/ph-ausseil/docstring-ai
"""
This module processes Python files to add docstrings using OpenAI's Assistant,
embeds the files in ChromaDB, and integrates with GitHub for pull request creation.

Functions:
- process_files_and_create_prs: Processes Python files, adds docstrings, and creates pull requests.
"""

import json
import os
import logging
from pathlib import Path
from typing import List, Dict
from functools import partial
from datetime import datetime

import openai
import tiktoken
from tqdm import tqdm

from docstring_ai.lib.utils import (
    ensure_docstring_header,
    check_git_repo,
    repo_has_uncommitted_changes,
    file_has_uncommitted_changes,
    load_cache,
    save_cache,
    get_python_files,
    sort_files_by_size,
    prompt_user_confirmation,
    show_diff,
    compute_sha256,
    traverse_repo,
    create_backup
)
from docstring_ai.lib.prompt_utils import (
    initialize_assistant,
    update_assistant_tool_resources,
    create_thread,
    construct_few_shot_prompt,
    create_file_with_docstring,
    generate_file_description
)
from docstring_ai.lib.chroma_utils import (
    initialize_chroma,
    get_or_create_collection,
    embed_and_store_files,
    store_class_summary
)
from docstring_ai.lib.docstring_utils import (
    DocstringExtractor,
)
from docstring_ai.lib.github_utils import create_github_pr, checkout_branch, commit_and_push_changes
from docstring_ai import (
    MAX_TOKENS, 
    CHROMA_COLLECTION_NAME, 
    CACHE_FILE_NAME, 
    DATA_PATH, 
    CONTEXT_SUMMARY_PATH,
)


def initialize_and_create_assistant(api_key: str):
    """
    Initializes the OpenAI Assistant and creates a new thread.
    Handles exceptions during initialization and thread creation.

    Args:
        api_key (str): OpenAI API key.

    Returns:
        Tuple[str, str]: Assistant ID and Thread ID.
    """
    try:
        assistant_id = initialize_assistant(api_key)
        if not assistant_id:
            logging.error("Failed to initialize OpenAI Assistant.")
            return None, None
        logging.debug(f"Assistant initialized with ID: {assistant_id}")

        thread_id = create_thread(api_key=api_key, assistant_id=assistant_id)
        if not thread_id:
            logging.error("Failed to create a new thread for the Assistant.")
            return assistant_id, None
        logging.debug(f"Thread created with ID: {thread_id}")

        return assistant_id, thread_id
    except Exception as e:
        logging.error(f"An error occurred during Assistant initialization: {e}")
        return None, None


def process_file_descriptions(
    files_to_process: List[str], 
    output_dir: Path, 
    assistant_id: str, 
    thread_id: str, 
    context_summary: List[Dict], 
    collection, 
    api_key: str, 
    repo_path: str  # Add repo_path parameter
) -> List[str]:
    """
    Generates detailed descriptions for files, embeds them into ChromaDB, and uploads to OpenAI, updating the Assistant's resources.

    Args:
        files_to_process (List[str]): List of file paths to generate descriptions for.
        output_dir (Path): Directory to store description files.
        assistant_id (str): OpenAI Assistant ID.
        thread_id (str): OpenAI Thread ID.
        context_summary (List[Dict]): Current context summary.
        collection: ChromaDB collection.
        api_key (str): OpenAI API key.
        repo_path (str): Repository path for computing relative paths.

    Returns:
        List[str]: List of successfully uploaded description file IDs.
    """
    file_descriptions_list = []
    description_file_ids = []
    for file in files_to_process:
        relative_path = str(Path(os.path.relpath(file, repo_path)))
        if not any(str(Path(entry["file"])) == relative_path for entry in context_summary):
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    file_content = f.read()

                file_description = generate_file_description(
                    assistant_id=assistant_id,
                    thread_id=thread_id, 
                    file_content=file_content
                )

                # Save description
                description_file_path = output_dir / Path(file).with_suffix('.txt')
                description_file_path.parent.mkdir(parents=True, exist_ok=True)
                with open(description_file_path, 'w', encoding='utf-8') as f:
                    f.write(file_description)

                file_descriptions_list.append(str(description_file_path))
                context_summary.append({"file": relative_path, "description": file_description})

            except Exception as e:
                logging.error(f"Failed to generate description for {file}: {e}")

    # Embed and upload descriptions
    if file_descriptions_list :
        embed_and_store_files(collection, file_descriptions_list, tags={"file_type": "description"})
        description_file_ids = upload_files_to_openai(file_descriptions_list)
    return description_file_ids


def process_files_and_create_prs(
    repo_path: str, 
    api_key: str, 
    create_pr: bool, 
    github_token: str, 
    github_repo: str, 
    branch_name: str, 
    pr_name: str, 
    pr_depth: int, 
    manual: bool,
    target_branch: str
) -> None:
    """
    Processes Python files in the specified repository, adds docstrings using OpenAI's Assistant,
    and creates pull requests on GitHub if specified.

    **Steps:**
    1. Setup and Initialization: Verify the presence of a Git repository, initialize ChromaDB, load cache.
    2. File Discovery and Preparation: Retrieve and sort Python files, filter out unchanged files.
    3. Embedding and Assistant Setup: Embed descriptions into ChromaDB and initialize Assistant.
    4. Docstring Generation and Processing: Process each Python file to generate and add docstrings.
    5. Cache Update and Pull Request Creation: Save context summary and update cache, create pull requests if specified.
    
    Args:
        repo_path (str): The path of the Git repository to process.
        api_key (str): The OpenAI API key for making requests.
        create_pr (bool): Flag indicating if pull requests should be created.
        github_token (str): The GitHub token for authentication.
        github_repo (str): The repository where pull requests will be made.
        branch_name (str): The base name of the branch for pull requests.
        pr_name (str): The name/title of the pull requests to create.
        pr_depth (int): The maximum depth to categorize folders for PR creation.
        manual (bool): Flag indicating if manual approval is required for changes.
        target_branch (str): The target branch for the PRs.
    
    Returns:
        None
    """
    ###
    ### INITIATE (Global Steps)
    ###
    openai.api_key = api_key

    # Check if Git is present
    git_present = check_git_repo(repo_path)
    if not git_present:
        logging.warning(f"Git repository not found at {repo_path}. Some operations may be skipped.")

    # Initialize ChromaDB
    logging.info("\nInitializing ChromaDB...")
    chroma_client = initialize_chroma()
    collection = get_or_create_collection(chroma_client, CHROMA_COLLECTION_NAME)

    # Load cache
    cache_path = os.path.join(repo_path, CACHE_FILE_NAME)
    cache = load_cache(cache_path)

    # Traverse repository and categorize folders based on pr_depth
    logging.debug("\nTraversing repository to categorize folders based on pr_depth...")
    folder_dict = traverse_repo(repo_path, pr_depth)
    logging.debug(f"Found {len(folder_dict)} depth levels up to {pr_depth}.")

    # Load Context Summary
    context_summary_full_path = os.path.join(repo_path, CONTEXT_SUMMARY_PATH)
    context_summary = []
    if os.path.exists(context_summary_full_path):
        try:
            with open(context_summary_full_path, 'r', encoding='utf-8') as f:
                context_summary = json.load(f)
            logging.info(f"Loaded context summary with {len(context_summary)} entries.")
        except Exception as e:
            logging.error(f"Error loading context summary: {e}")

    # Initialize Assistant and create thread
    assistant_id, thread_id = initialize_and_create_assistant(api_key)
    if not assistant_id or not thread_id:
        logging.error("Failed to initialize Assistant or create a thread. Exiting.")
        return

    # Ensure the './data/' directory exists
    output_dir = Path(DATA_PATH)
    output_dir.mkdir(parents=True, exist_ok=True)

    ###
    ### Steps 9 to 11: Create Missing Context, Embed, and Upload Descriptions
    ###
    logging.info("\nCreating missing context for files...")
    # Gather all files to process for descriptions
    python_files = get_python_files(repo_path)
    logging.info(f"Found {len(python_files)} Python files to process for descriptions.")

    # Sort files by size (ascending)
    python_files_sorted = sort_files_by_size(python_files)

    # Compute SHA-256 hashes and filter out unchanged files
    logging.info("\nChecking file hashes for descriptions...")
    files_to_describe = filter_files_by_hash(python_files_sorted, repo_path, cache)
    logging.info(f"{len(files_to_describe)} files need descriptions after cache check.")

    if files_to_describe:
        description_file_ids = process_file_descriptions(
            files_to_process=files_to_describe,
            output_dir=output_dir,
            assistant_id=assistant_id,
            thread_id=thread_id,
            context_summary=context_summary,
            collection=collection,
            api_key=api_key,
            repo_path=repo_path
        )
        if not description_file_ids:
            logging.warning("No descriptions were uploaded to OpenAI. Proceeding without descriptions.")
    else:
        logging.info("No new file descriptions needed.")

    ###
    ### PROCESS PER FOLDER AND CREATE PRs (Per-Folder Steps)
    ###
    logging.info("\nProcessing folders and creating Pull Requests...")
    for depth, folders in reversed(folder_dict.items()):
        for folder in folders:
            python_files_to_process = []
            logging.info(f"\nProcessing folder '{folder}'...")

            i = 0
            length = len(files_to_describe)
            while i < length: 
                if (files_to_describe[0].startswith(str(Path(folder))) or depth == 0):
                    python_files_to_process.append(files_to_describe.pop(0))
                
                i += 1
            
            if not python_files_to_process:
                logging.info(f"No Python files found in folder '{folder}'. Skipping.")
                continue  # Skip folders with no Python files

            # Step 12: Process Each Python File for Docstrings
            logging.info(f"{folder}' - Processing : {' '.join(python_files_to_process)}  to add docstrings...")
            with tqdm(total=len(python_files_to_process), desc=f"Adding docstrings in '{folder}'", unit="file", dynamic_ncols=True) as pbar:
                for python_file_path in python_files_to_process:
                    process_single_file(
                        python_file_path=python_file_path,
                        repo_path=repo_path,
                        assistant_id=assistant_id,
                        thread_id=thread_id,
                        collection=collection,
                        context_summary=context_summary,
                        cache=cache,
                        manual=manual
                    )
                    pbar.update(1)

            # Step 13: Save Context Summary
            try:
                with open(context_summary_full_path, "w", encoding='utf-8') as f:
                    json.dump(context_summary, f, indent=2)
                logging.info(f"\nDocstring generation completed for folder '{folder}'. Context summary saved to '{context_summary_full_path}'.")
            except Exception as e:
                logging.error(f"Error saving context summary for folder '{folder}': {e}")

            # Step 14: Save Cache
            save_cache(cache_path, cache)

            # Step 15: Create Pull Requests Based on pr_depth
            if create_pr and git_present and github_token and github_repo:
                if manual:
                    # Show summary of PR to be created and ask for confirmation
                    print(f"\nPull Request to be created for folder: '{folder}'")
                    if not prompt_user_confirmation(f"Do you want to proceed with creating a Pull Request for '{folder}'?"):
                        logging.info(f"Pull Request creation for folder '{folder}' aborted by the user.")
                        continue

                folder_rel_path = os.path.relpath(folder, repo_path)
                logging.info(f"Creating Pull Request for the folder {folder_rel_path}")
                # Generate a unique branch name for the folder

                # # Commit and push changes to the branch
                # commit_message = f"[Docstring-AI] Add docstrings via Docstring-AI script for folder {folder_rel_path}"
                # if not commit_and_push_changes(repo_path, folder_branch_name, commit_message):
                #     logging.error(f"Failed to commit and push changes for folder '{folder_rel_path}'. Skipping PR creation.")
                #     continue

                # Create GitHub PR
                pr_created = create_github_pr(
                    repo_path=repo_path, 
                    github_token=github_token, 
                    github_repo=github_repo, 
                    branch_name=branch_name  + f"_{folder_rel_path.replace(os.sep, '_')}", 
                    pr_name=pr_name + f" `{folder_rel_path}`",
                    target_branch=target_branch
                )

                if pr_created:
                    logging.info(f"Pull Request created successfully for folder '{folder_rel_path}'.")
                else:
                    logging.error(f"Failed to create Pull Request for folder '{folder_rel_path}'.")

    logging.info("\nAll folders processed successfully.")


def process_single_file(
    python_file_path: str,
    repo_path: str,
    assistant_id: str,
    thread_id: str,
    collection,
    context_summary: list,
    cache: dict,
    manual: bool
) -> None:
    """
    Processes a single Python file: adds docstrings, updates context, and handles caching.

    **Steps:**
    1. File Reading and Initial Setup: Compute the file's relative path within the repository and read its content.
    2. Class Parsing and Context Retrieval: Parse the file for class definitions and retrieve relevant context summaries.
    3. Few-Shot Prompt Construction: Construct a few-shot prompt using retrieved context.
    4. File Description Generation: Check the cache and generate a file description if not cached.
    5. Docstring Generation: Use the Assistant to generate docstrings for the code.
    6. Manual Validation (Optional): Display changes for user approval if manual validation is required.
    7. File Update: Backup and update the file with new content.
    8. Context Summary and Cache Updates: Create an updated file description and update the cache.
    9. Class Summaries: Extract and store summaries for modified classes.

    Args:
        python_file_path (str): Path to the Python file.
        repo_path (str): Repository path.
        assistant_id (str): OpenAI Assistant ID.
        thread_id (str): OpenAI Thread ID.
        collection: ChromaDB collection.
        context_summary (list): Current context summary.
        cache (dict): Cache dictionary.
        manual (bool): Flag indicating if manual approval is required.

    Returns:
        None
    """
    try:
        relative_path = os.path.relpath(python_file_path, repo_path)
        with open(python_file_path, 'r', encoding='utf-8') as f:
            original_code = f.read()
    except Exception as e:
        logging.error(f"Error reading file {python_file_path}: {e}")
        return

    # Check if file is cached and has existing description
    cached_entry = next((item for item in context_summary if str(Path(item["file"])) == str(Path(relative_path))), None)
    if cached_entry:
        file_description = cached_entry.get("description", "")
        logging.debug(f"Using cached description for {python_file_path}.")
    else: 
        logging.error("No file description found in context_summary. Please ensure descriptions are generated before processing files.")
        file_description = ""  # Initialize to empty string or handle accordingly

    extractor = DocstringExtractor(file_path=python_file_path)
    extractor.process()
    classes = extractor.process_imports(package='docstring_ai.lib')    

    # Construct few-shot prompt
    few_shot_prompt = construct_few_shot_prompt(
        collection=collection, 
        classes=classes, 
        max_tokens=MAX_TOKENS - len(original_code),
        context=file_description
    )

    # Add docstrings using Assistant's API
    logging.debug(f"Generating new docstrings for: {python_file_path}")

    # Create a partial function for approval and saving
    patched_approve_and_save_file = partial(
        approve_and_save_file,
        original_code=original_code,
        python_file_path=python_file_path,
        repo_path=repo_path,
        manual=manual,
        context_summary=context_summary,
        cache=cache,
        collection=collection,
        assistant_id=assistant_id,
        thread_id=thread_id,
    )

    # Generate and apply docstrings
    try:
        result = create_file_with_docstring(
            assistant_id=assistant_id,
            thread_id=thread_id,
            code=original_code,
            context=few_shot_prompt,
            functions={"write_file_with_new_docstring": patched_approve_and_save_file}
        )
    except Exception as e:
        logging.error(f"Failed to generate docstrings for {python_file_path}: {e}")
        return

    if result:
        logging.info(f"Docstrings successfully added and saved for {python_file_path}.")
    else:
        logging.warning(f"Docstrings not added for {python_file_path}.")


def approve_and_save_file(
    new_file_content: str,
    original_code: str,
    python_file_path: str,
    repo_path: str,
    manual: bool,
    context_summary: list,
    cache: dict,
    collection,
    assistant_id: str,
    thread_id: str,
) -> bool:
    """
    Approves and saves the modified code, handles manual validation, updates context, and cache.

    Args:
        new_file_content (str): The code after adding docstrings.
        original_code (str): The original code before modification.
        python_file_path (str): Path to the Python file.
        repo_path (str): Repository path.
        manual (bool): Flag indicating if manual approval is required.
        context_summary (list): Current context summary.
        cache (dict): Cache dictionary.
        collection: ChromaDB collection.
        assistant_id (str): OpenAI Assistant ID.
        thread_id (str): OpenAI Thread ID.

    Returns:
        bool: True if the file was successfully updated and saved, False otherwise.
    """
    new_file_content = new_file_content.replace('` ``', '```')
    # Check if there's any change in the file content
    if not new_file_content:
        logging.info(f"No changes made to {python_file_path}.")
        return False  # Indicate that no changes were made

    # Ensure the header is added if not present
    try:
        new_file_content = ensure_docstring_header(new_file_content)
    except Exception as e:
        logging.error(f"Error ensuring docstring header for {python_file_path}: {e}")
        return False

    try:
        # Backup and update the file
        create_backup(python_file_path)
        with open(python_file_path, "w", encoding="utf-8") as f:
            f.write(new_file_content)

        logging.info(f"Updated docstrings in {python_file_path}")

        # Update cache
        new_hash = compute_sha256(python_file_path)
        relative_path = os.path.relpath(python_file_path, repo_path)
        cache[relative_path] = new_hash

        logging.info(f"Cache updated for {python_file_path}")
        return True

    except Exception as e:
        logging.error(f"Error updating file {python_file_path}: {e}")
        return False


def filter_files_by_hash(file_paths: List[str], repo_path: str, cache: Dict[str, str]) -> List[str]:
    """
    Filters files based on SHA-256 hash and cache.

    Args:
        file_paths (List[str]): List of file paths to filter.
        repo_path (str): Path to the repository.
        cache (Dict[str, str]): Cache dictionary storing file hashes.

    Returns:
        List[str]: List of file paths that need processing.
    """
    changed_files = []
    logging.info("Starting file hash verification...")
    
    with tqdm(total=len(file_paths), desc="Verifying file hashes", unit="file") as pbar:
        for file_path in file_paths:
            try:
                current_hash = compute_sha256(file_path)
                relative_path = os.path.relpath(file_path, repo_path)
                cached_hash = cache.get(relative_path)
                
                if current_hash != cached_hash:
                    changed_files.append(file_path)
            except Exception as e:
                logging.error(f"Error verifying hash for {file_path}: {e}")
            finally:
                pbar.update(1)
    
    logging.info(f"Hash verification completed. {len(changed_files)} files require processing.")
    return changed_files


def upload_files_to_openai(file_paths: List[str]) -> List[str]:
    """
    Uploads files to OpenAI and returns file IDs.

    Args:
        file_paths (List[str]): List of file paths to upload.

    Returns:
        List[str]: List of file IDs.
    """
    file_ids = []
    for file_path in file_paths:
        try:
            with open(file_path, "rb") as f:
                response = openai.files.create(
                    file=f,
                    purpose="assistants"
                )
            file_ids.append(response.id)
        except Exception as e:
            logging.error(f"Failed to upload {file_path}: {e}")
    return file_ids
