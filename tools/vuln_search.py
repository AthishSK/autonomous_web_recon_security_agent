import requests
import time

def cve_search(keyword: str) -> dict:
    """Searches the NIST NVD database for CVEs related to a keyword."""
    # NIST API v2 endpoint
    url = f"https://services.nvd.nist.gov/rest/json/cves/2.0?keywordSearch={keyword}&resultsPerPage=5"
    
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            data = response.json()
            vulnerabilities = data.get('vulnerabilities', [])
            results = []
            
            for vuln in vulnerabilities:
                cve = vuln.get('cve', {})
                results.append({
                    "id": cve.get('id'),
                    "description": cve.get('descriptions', [{}])[0].get('value'),
                    "published": cve.get('published')
                })
            return {"cves": results}
        elif response.status_code == 403:
            return {"error": "Rate limited by NIST NVD API. Try adding an API key."}
        return {"error": f"NIST API returned status {response.status_code}"}
    except Exception as e:
        return {"error": f"CVE search failed: {str(e)}"}