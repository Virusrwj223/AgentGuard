"""Real subprocess boundary driven by a synthetic console on POSIX."""
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from app.pat import Pat
@unittest.skipIf(os.name=="nt","POSIX runner fixture uses an absolute interpreter path")
class Runner(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.script=self.root/"console.py"
        self.script.write_text("""import sys,time
from pathlib import Path
model=Path(sys.argv[-2]).read_text()
assert sys.argv[1:6]==['-pcsp','-engine','0','-behavior','0']
if model=='empty':sys.exit(0)
if model=='fail':sys.exit(9)
if model=='timeout':time.sleep(12)
p='0.001' if '999 :' in model else '0.25'
if model=='bad':p='NaN'
Path(sys.argv[-1]).write_text('The Assertion (Business() reaches economicSuccess with pmin) is Valid with Min Probability '+p+';')
""")
        self.cfg={"AG_PAT_RUNTIME":sys.executable,"AG_PAT_EXE":str(self.script),"AG_PAT_SHA256":hashlib.sha256(self.script.read_bytes()).hexdigest(),"AG_PAT_VERSION":"test fixture"}
    def test_calibration_and_errors(self):
        r=Pat(self.cfg)
        self.assertTrue(r.ready)
        for text,status in [("empty","TOOL_ERROR"),("fail","TOOL_ERROR"),("bad","PARSE_ERROR"),("timeout","TIMEOUT")]:
            self.assertEqual(r.run(text)["status"],status)
    def test_wrong_hash(self):
        self.assertFalse(Pat(self.cfg|{"AG_PAT_SHA256":"0"*64}).ready)
