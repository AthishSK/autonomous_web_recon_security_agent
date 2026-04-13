# test_registry.py

import json
import logging
from tools.registry import execute_tool

# Suppress the verbose logging from registry.py during testing to keep output clean
logging.getLogger().setLevel(logging.CRITICAL)

def run_comprehensive_tests():
    print("======================================================")
    print("===  AUTONOMOUS AGENT TOOL SUITE - DETAILED TESTS  ===")
    print("======================================================\n")

    # Define all our test cases in a clean array
    test_cases = [
        # --- 1. PASSIVE RECON TESTS ---
        {
            "name": "WHOIS Lookup (Standard Domain)",
            "tool": "whois_lookup",
            "args": {"domain": "example.com"}
        },
        {
            "name": "DNS Lookup (A Record)",
            "tool": "dns_lookup",
            "args": {"domain": "example.com", "record_type": "A"}
        },
        {
            "name": "Subdomain Enumeration (crt.sh)",
            "tool": "subdomain_enum",
            "args": {"domain": "example.com"}
        },

        # --- 2. ACTIVE WEB RECON TESTS ---
        {
            "name": "HTTP Headers Analysis",
            "tool": "http_headers",
            "args": {"url": "https://example.com"}
        },
        {
            "name": "Tech Fingerprint (Custom Lightweight)",
            "tool": "tech_fingerprint",
            "args": {"url": "https://example.com"}
        },
        {
            "name": "SSL/TLS Certificate Check",
            "tool": "ssl_check",
            "args": {"hostname": "example.com"}
        },

        # --- 3. VULNERABILITY & PORT SCANNING ---
        {
            "name": "CVE Search (NIST NVD API)",
            "tool": "cve_search",
            "args": {"keyword": "nginx"} # Searching for general nginx CVEs
        },
        {
            "name": "Nmap Port Scan (Authorized Target)",
            "tool": "nmap_scan",
            "args": {"target": "scanme.nmap.org", "arguments": "-F"} # -F is Fast Scan
        },

        # --- 4. LLM HALLUCINATION & ERROR HANDLING TESTS ---
        {
            "name": "ERROR: Hallucinated Tool Name",
            "tool": "hack_the_mainframe",
            "args": {"ip_address": "127.0.0.1"}
        },
        {
            "name": "ERROR: Missing Required Arguments",
            "tool": "dns_lookup",
            "args": {"record_type": "TXT"} # Notice 'domain' is missing
        },
        {
            "name": "ERROR: Malformed URL/Input",
            "tool": "http_headers",
            "args": {"url": "not_a_real_website_12345.com"} 
        }
    ]

    # Loop through and execute each test
    for i, test in enumerate(test_cases, 1):
        print(f"[*] TEST {i}/{len(test_cases)}: {test['name']}")
        print(f"    Tool: {test['tool']} | Args: {test['args']}")
        print("    " + "-" * 40)
        
        # Execute the tool via the registry
        result = execute_tool(test['tool'], test['args'])
        
        # Format and print the result
        formatted_result = json.dumps(result, indent=2)
        
        # Indent the JSON output so it's easier to read under the test header
        for line in formatted_result.split('\n'):
            print(f"    {line}")
            
        print("\n" + "=" * 54 + "\n")

if __name__ == "__main__":
    run_comprehensive_tests()