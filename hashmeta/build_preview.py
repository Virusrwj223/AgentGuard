"""Create a public reference page using only the owner's supplied public destinations."""
import argparse
import html
from pathlib import Path
from urllib.parse import urlsplit
def https(value):
    u=urlsplit(value)
    if u.scheme!="https" or not u.hostname or u.username or u.password:
        raise argparse.ArgumentTypeError("Provide an HTTPS destination without credentials")
    return html.escape(value,quote=True)
p=argparse.ArgumentParser()
p.add_argument("--demo-url",type=https,required=True);p.add_argument("--contact-url",type=https,required=True)
p.add_argument("--output",type=Path,default=Path("site-preview.html"))
a=p.parse_args()
page="""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AgentGuard | Governed travel negotiations</title><style>
*{box-sizing:border-box}body{margin:0;color:#183c36;background:#f5f5ec;font:18px/1.55 system-ui,sans-serif}header,section,footer{padding:28px max(calc((100vw - 1000px)/2),6vw)}header{display:flex;justify-content:space-between;border-bottom:1px solid #b7c9b9}a{color:inherit}h1,h2{font-family:Georgia,serif;font-weight:400;line-height:1.08}h1{font-size:clamp(44px,7vw,84px);max-width:17ch;margin:50px 0 20px}h2{font-size:40px}section p{max-width:65ch}.button{display:inline-block;background:#235942;color:white;padding:12px 22px;border-radius:6px;text-decoration:none}.case{background:#dae7d6;margin-top:36px}.case table{border-collapse:collapse;width:min(100%,570px)}th,td{text-align:left;border-bottom:1px solid #a4b6a5;padding:10px 0}footer{font-size:14px;border-top:1px solid #b7c9b9}</style>
<header><strong>◈ AgentGuard</strong><nav><a href="#how">How it works</a> · <a href="CONTACT">Contact</a></nav></header><main><section><h1>Negotiate with the cost in view.</h1><p>Before your travel agency promises a customer a new date or price, compare what each offer could cost across supplier outcomes.</p><a class="button" href="DEMO">Explore the demo →</a></section><section class="case"><h2>One trip. Three responses.</h2><p>A traveller wants to move a booking by 30 days at no extra cost. What changes if the agency waits for a binding supplier hold?</p><table><tr><th>Agency response</th><th>Expected value</th></tr><tr><td>Keep the original booking</td><td>−SGD 150</td></tr><tr><td>Guarantee the change now</td><td>−SGD 2,094</td></tr><tr><td>Wait for a binding hold</td><td>−SGD 94</td></tr></table><p>Synthetic reference scenario. Northline Travel is fictional. The acquisition-to-negotiation case was inspired by Mesho Travel &amp; Tours.</p></section><section id="how"><h2>How a proposal becomes an offer</h2><ol><li>The sales workflow interprets the request and proposes supported options.</li><li>The compiler builds a model from approved records. A configured PAT runner checks the same economic query for each option.</li><li>The customer receives the exact authorized terms. A conditional booking changes only after a binding hold.</li></ol><h2>What the model covers</h2><p>Results depend on the approved costs, probabilities and 90-day horizon. The exact preview sends decisions to review. All supplier and payment operations are simulated.</p></section></main><footer>AgentGuard · Travel governance demonstration</footer></html>"""
a.output.write_text(page.replace('href="DEMO"','href="'+a.demo_url+'"').replace('href="CONTACT"','href="'+a.contact_url+'"'),encoding="utf-8")
