"""The startup selector never opens production LDAP data in these tests."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE=Path(__file__).with_name('start-compatible.sh')

class CompatibleStartup(unittest.TestCase):
    def run_case(self,existing=False,partial=False):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);config=root/'config';data=root/'data'
            config.mkdir();data.mkdir()
            if existing:(config/'cn=config.ldif').write_text('existing configuration')
            if partial:(data/'data.mdb').write_text('existing database')
            for name in ['slapd','bootstrap']:
                p=root/name;p.write_text('#!/bin/sh\nprintf "'+name+' %s\\n" "$*"\n');p.chmod(0o700)
            script=SOURCE.read_text().replace('/etc/ldap/slapd.d',str(config)).replace('/var/lib/ldap',str(data)).replace('/usr/sbin/slapd',str(root/'slapd')).replace('/container/tool/run',str(root/'bootstrap'))
            return subprocess.run(['sh','-c',script],capture_output=True,text=True)
    def test_existing_configuration_keeps_direct_slapd(self):
        p=self.run_case(existing=True);self.assertEqual(p.returncode,0);self.assertIn('slapd -d 0',p.stdout);self.assertNotIn('bootstrap',p.stdout)
    def test_empty_deployment_keeps_original_bootstrap(self):
        p=self.run_case();self.assertEqual(p.returncode,0);self.assertIn('bootstrap --copy-service',p.stdout)
    def test_partial_database_is_never_overwritten(self):
        p=self.run_case(partial=True);self.assertNotEqual(p.returncode,0);self.assertIn('refusing',p.stderr);self.assertEqual(p.stdout,'')

if __name__=='__main__':unittest.main()
