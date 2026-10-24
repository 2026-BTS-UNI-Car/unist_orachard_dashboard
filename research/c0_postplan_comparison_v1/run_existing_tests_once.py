"""Run the related existing synthetic observation suite against this overlay."""
from pathlib import Path
import importlib.util,sys,hashlib,io,json,time,unittest
K=Path(__file__).resolve().parent;U=Path('D:/Develop/UBTS');B=U/'dashboard-live-raw'
def main():
 if sys.argv[1:]!=['--run-pure']:print('{"default_OFF":true,"device_IO":false}');return
 started=time.monotonic();sys.path[:0]=[str(B/'tests'),str(B)]
 import dashboard_server
 path=K/'overlays/dashboard_server/autonomy_observation.py'
 spec=importlib.util.spec_from_file_location('dashboard_server.autonomy_observation',path);module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
 log=io.StringIO();suite=unittest.TestLoader().loadTestsFromName('test_autonomy_observation');result=unittest.TextTestRunner(stream=log,verbosity=1).run(suite)
 value=dict(ok=result.wasSuccessful(),existing_tests=result.testsRun,errors=len(result.errors),failures=len(result.failures),skipped=len(result.skipped),device_IO=False,SDK_ROS_or_motor=False,software_unit_threads_only=True,
  overlay_SHA256=hashlib.sha256(path.read_bytes()).hexdigest(),existing_test_SHA256=hashlib.sha256((B/'tests/test_autonomy_observation.py').read_bytes()).hexdigest(),log_SHA256=hashlib.sha256(log.getvalue().encode()).hexdigest(),wall_s=time.monotonic()-started)
 if not result.wasSuccessful():print(log.getvalue()[:32768])
 print(json.dumps(value,sort_keys=True));raise SystemExit(0 if value['ok']else 1)
if __name__=='__main__':main()
