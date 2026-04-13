# tools/registry.py

import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Import core tools directly so they fail loudly if dependencies are missing
from tools.nmap_scan import nmap_scan
from tools.ssl_check import ssl_check
from tools.passive_recon import whois_lookup, dns_lookup, subdomain_enum
from tools.web_recon import tech_fingerprint
from tools.vuln_search import cve_search

# ---------------------------------------------------------
# 2. Map String Names (from the LLM) to Python Functions
# ---------------------------------------------------------
# The LLM will output JSON like: {"tool": "dns_lookup", "args": {"domain": "example.com"}}
# This dictionary lets us safely map the string "dns_lookup" to the actual function.

AVAILABLE_TOOLS = {
    "nmap_scan": nmap_scan,
    "ssl_check": ssl_check,
    "whois_lookup": whois_lookup,
    "dns_lookup": dns_lookup,
    "subdomain_enum": subdomain_enum,
    # "http_headers": http_headers,
    "tech_fingerprint": tech_fingerprint,
    "cve_search": cve_search
}

# ---------------------------------------------------------
# 3. The Execution Engine
# ---------------------------------------------------------

def execute_tool(tool_name: str, arguments: dict) -> dict:
    """
    Executes a security tool based on the name and arguments provided by the LLM.
    
    Args:
        tool_name (str): The name of the tool to run (must be in AVAILABLE_TOOLS).
        arguments (dict): The arguments to pass to the tool (e.g., {"domain": "example.com"}).
        
    Returns:
        dict: The result of the tool execution, or an error message.
    """
    logging.info(f"Agent requested tool: '{tool_name}' with args: {arguments}")

    # 1. Security/Validation Check: Does the tool exist?
    if tool_name not in AVAILABLE_TOOLS:
        error_msg = f"Tool '{tool_name}' is not recognized. Available tools are: {list(AVAILABLE_TOOLS.keys())}"
        logging.error(error_msg)
        # We return the error as a dict so the LLM can read it and realize it made a mistake
        return {"error": error_msg}
    
    # 2. Execution
    try:
        # Fetch the actual Python function from our dictionary
        tool_function = AVAILABLE_TOOLS[tool_name]
        
        # Execute the function, unpacking the dictionary into keyword arguments (**arguments)
        result = tool_function(**arguments)
        
        logging.info(f"Tool '{tool_name}' executed successfully.")
        return result
        
    # 3. Error Handling: Catch-all for tool crashes (e.g., network timeout, bad input)
    except TypeError as e:
        error_msg = f"Invalid arguments passed to '{tool_name}'. Error: {str(e)}"
        logging.error(error_msg)
        return {"error": error_msg}
        
    except Exception as e:
        error_msg = f"Execution of '{tool_name}' failed unexpectedly: {str(e)}"
        logging.error(error_msg)
        return {"error": error_msg}