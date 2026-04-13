"""
Tool: nmap_scan
Purpose: Perform a network port scan on a target host to discover:
  - Which ports are open (services are running)
  - What service/software is listening on each port
  - The version of each detected service
 
This is almost always the FIRST tool the agent calls, because knowing which
ports are open determines what other tools are worth running.
  → Port 443 open  → run ssl_check
  → Port 80 open   → run http_headers, tech_fingerprint
  → Port 22 open   → flag SSH version for CVE lookup
  → Any open port  → run cve_search on the detected service + version
 
Requires: python-nmap (`pip install python-nmap`)
Requires: nmap binary installed on the system (`apt install nmap` / `brew install nmap`)
"""

import nmap

def nmap_scan(target: str, arguments: str = '-F') -> dict:
    """Performs a network scan using nmap. Defaults to Fast scan (-F)."""
    nm = nmap.PortScanner()
    try:
        nm.scan(hosts=target, arguments=arguments)
        scan_results = {}
        
        for host in nm.all_hosts():
            scan_results[host] = {'state': nm[host].state(), 'protocols': {}}
            for proto in nm[host].all_protocols():
                ports = nm[host][proto].keys()
                scan_results[host]['protocols'][proto] = {}
                for port in ports:
                    scan_results[host]['protocols'][proto][port] = nm[host][proto][port]
                    
        return {"scan_results": scan_results}
    except Exception as e:
        return {"error": f"Nmap scan failed: {str(e)}"}