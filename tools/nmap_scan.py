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