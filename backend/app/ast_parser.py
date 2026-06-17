import ast
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def parse_python_file(file_content: str, filepath: str) -> List[Dict[str, Any]]:
    """
    Parses Python source code and breaks it down into chunks of classes, methods, 
    top-level functions, and module-level statements.
    
    Returns a list of dictionaries, each containing:
        - name: Name of the function, class, or module
        - type: 'class', 'function', 'method', or 'module'
        - start_line: 1-indexed start line
        - end_line: 1-indexed end line
        - code_content: The actual string content of the chunk
    """
    chunks = []
    lines = file_content.splitlines()
    total_lines = len(lines)
    
    if total_lines == 0:
        return []

    try:
        tree = ast.parse(file_content)
    except Exception as e:
        logger.warning(f"Failed to parse AST for {filepath}: {e}. Falling back to whole file.")
        # Fallback to returning the entire file as a single module chunk
        return [{
            "name": "module",
            "type": "module",
            "start_line": 1,
            "end_line": total_lines,
            "code_content": f"# File: {filepath}\n{file_content}"
        }]

    # Track which lines have been processed in fine-grained chunks (functions/methods)
    # to extract remaining code as module-level statements.
    fine_grained_lines = set()

    for node in tree.body:
        # 1. Handle Top-level Class Definitions
        if isinstance(node, ast.ClassDef):
            class_start = node.lineno
            class_end = node.end_lineno if node.end_lineno else total_lines
            
            # Find class docstring or header length to create a class definition chunk
            # Usually we take from the class definition line down to the start of the first method or body node
            header_end = class_end
            methods = []
            
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(child)
                    if child.lineno > class_start and child.lineno - 1 < header_end:
                        header_end = child.lineno - 1
            
            # Add class header/docstring chunk
            class_header_code = "\n".join(lines[class_start - 1 : header_end])
            chunks.append({
                "name": node.name,
                "type": "class",
                "start_line": class_start,
                "end_line": header_end,
                "code_content": f"# File: {filepath}\n# Class: {node.name}\n{class_header_code}"
            })
            
            # Extract each method
            for method in methods:
                m_start = method.lineno
                m_end = method.end_lineno if method.end_lineno else class_end
                method_code = "\n".join(lines[m_start - 1 : m_end])
                chunks.append({
                    "name": f"{node.name}.{method.name}",
                    "type": "method",
                    "start_line": m_start,
                    "end_line": m_end,
                    "code_content": f"# File: {filepath}\n# Class: {node.name} -> Method: {method.name}\n{method_code}"
                })
                for line_idx in range(m_start, m_end + 1):
                    fine_grained_lines.add(line_idx)
            
            # Mark the rest of the class lines as processed
            for line_idx in range(class_start, class_end + 1):
                fine_grained_lines.add(line_idx)

        # 2. Handle Top-level Function Definitions
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_start = node.lineno
            func_end = node.end_lineno if node.end_lineno else total_lines
            func_code = "\n".join(lines[func_start - 1 : func_end])
            chunks.append({
                "name": node.name,
                "type": "function",
                "start_line": func_start,
                "end_line": func_end,
                "code_content": f"# File: {filepath}\n# Function: {node.name}\n{func_code}"
            })
            for line_idx in range(func_start, func_end + 1):
                fine_grained_lines.add(line_idx)

    # 3. Handle Module-level Statements (Imports, Global Variables, etc.)
    # Group contiguous unprocessed lines into module chunks
    current_module_chunk = []
    chunk_start = None
    
    for i in range(1, total_lines + 1):
        if i not in fine_grained_lines:
            if chunk_start is None:
                chunk_start = i
            current_module_chunk.append(lines[i - 1])
        else:
            if current_module_chunk:
                code_str = "\n".join(current_module_chunk).strip()
                if code_str:
                    chunks.append({
                        "name": "module_level",
                        "type": "module",
                        "start_line": chunk_start,
                        "end_line": i - 1,
                        "code_content": f"# File: {filepath}\n# Module-Level Scope\n" + "\n".join(current_module_chunk)
                    })
                current_module_chunk = []
                chunk_start = None
                
    if current_module_chunk:
        code_str = "\n".join(current_module_chunk).strip()
        if code_str:
            chunks.append({
                "name": "module_level",
                "type": "module",
                "start_line": chunk_start,
                "end_line": total_lines,
                "code_content": f"# File: {filepath}\n# Module-Level Scope\n" + "\n".join(current_module_chunk)
            })

    return chunks
