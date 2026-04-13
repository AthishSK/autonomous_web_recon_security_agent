import requests

def tech_fingerprint(url: str) -> dict:
    """A lightweight fingerprinter checking headers and HTML content without Wappalyzer."""
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
        
    technologies = []
    try:
        # verify=False prevents SSL errors on sketchier targets, but you can remove it
        response = requests.get(url, timeout=10, verify=False)
        headers = response.headers
        html = response.text.lower()
        
        # 1. Check Server Headers
        server = headers.get('Server', '').lower()
        if 'nginx' in server: technologies.append('Nginx')
        elif 'apache' in server: technologies.append('Apache')
        elif 'cloudflare' in server: technologies.append('Cloudflare')
        
        # 2. Check X-Powered-By
        x_powered_by = headers.get('X-Powered-By', '').lower()
        if 'php' in x_powered_by: technologies.append('PHP')
        elif 'express' in x_powered_by: technologies.append('Express.js')
        elif 'asp.net' in x_powered_by: technologies.append('ASP.NET')
        
        # 3. Check HTML content for common CMS and Frameworks
        if 'wp-content' in html: technologies.append('WordPress')
        if 'react' in html or 'data-reactroot' in html: technologies.append('React')
        if 'vue' in html: technologies.append('Vue.js')
        if 'next.js' in html or '_next/static' in html: technologies.append('Next.js')
        if 'laravel' in html: technologies.append('Laravel')
        
        return {"technologies": list(set(technologies))}
        
    except Exception as e:
        return {"error": f"Fingerprinting failed: {str(e)}"}