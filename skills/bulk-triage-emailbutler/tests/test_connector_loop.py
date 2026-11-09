from pathlib import Path
import shutil
import subprocess
import unittest

BRIDGE = Path(__file__).resolve().parents[1] / "scripts" / "connector_loop.js"


@unittest.skipUnless(shutil.which("node"), "Node is needed only to test the host JavaScript bridge")
class ConnectorLoopTests(unittest.TestCase):
    def check(self, code):
        script = "const assert=require('node:assert/strict');const bridge=require(process.argv[1]);\n(async()=>{\n" + code + "\n})().catch(error=>{console.error(error);process.exitCode=1});"
        result = subprocess.run(["node", "-e", script, str(BRIDGE)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_normalization_keeps_only_metadata_and_known_retry_fields(self):
        self.check(r'''
const raw={content:[{type:"text",text:JSON.stringify({id:"synthetic",labelIds:["INBOX"],body:"private body",headers:{Authorization:"secret","Retry-After":"2"}})}]};
const value=bridge.normalize(raw);
assert.equal(value.id,"synthetic");assert.equal(value.body,undefined);assert.equal(value.headers.Authorization,undefined);assert.equal(value.headers["Retry-After"],"2");
assert.deepEqual(bridge.normalize({isError:true,structuredContent:{status:429,headers:{"Retry-After":"3"}}}).error.status,429);
assert.equal(bridge.normalize({content:[{type:"text",text:"Not structured JSON"}]}).error.code,"unstructured_tool_response");
''')

    def test_bound_loop_dispatches_without_returning_raw_tool_results(self):
        self.check(r'''
const runId="R-"+"a".repeat(32);let step=0;const packets=[];
const request=(id,operation)=>({session_id:1,output:JSON.stringify({type:"request",id,operation,arguments:{message_ids:["synthetic"]}})+"\n"});
const tools={exec_command:async()=>request(1,"gmail.profile"),write_stdin:async args=>{const packet=JSON.parse(args.chars);packets.push(packet);step++;return step===1?request(2,"gmail.modify"):{exit_code:0,output:JSON.stringify({type:"summary",status:"complete",counts:{verified:1}})+"\n"}}};
let writes=0;const bindings={"gmail.profile":async()=>({emailAddress:"owner@example.com",body:"discard"}),"gmail.labels":async()=>({labels:[]}),"gmail.read":async()=>({}),"gmail.modify":async()=>{writes++;return {body:"discard",success:true}}};
const result=await bridge.run({tools,bindings,provider:"gmail",script:"/synthetic/execute_bulk.py",stateDir:"/synthetic/state",runId,approvedRunId:runId});
assert.equal(writes,1);assert.equal(result.status,"complete");assert.equal(result.counts.verified,1);assert.equal(packets[0].result.body,undefined);assert.equal(result.body,undefined);
''')

    def test_missing_bindings_and_wrong_approval_never_launch(self):
        self.check(r'''
let launched=0;const tools={exec_command:async()=>{launched++;throw Error("Must not launch")},write_stdin:async()=>{}};
const runId="R-"+"a".repeat(32);
await assert.rejects(bridge.run({tools,bindings:{},provider:"outlook",runId,approvedRunId:runId}));
const bindings=Object.fromEntries(["gmail.profile","gmail.labels","gmail.read","gmail.modify"].map(key=>[key,async()=>({})]));
await assert.rejects(bridge.run({tools,bindings,provider:"gmail",runId,approvedRunId:"another"}));assert.equal(launched,0);
''')

    def test_cancellation_is_forwarded_without_dispatching_a_connector(self):
        self.check(r'''
const runId="R-"+"b".repeat(32);let calls=0;
const bindings=Object.fromEntries(["gmail.profile","gmail.labels","gmail.read","gmail.modify"].map(key=>[key,async()=>{calls++;return {}}]));
const tools={exec_command:async()=>({session_id:7,output:JSON.stringify({type:"request",id:1,operation:"gmail.profile",arguments:{}})+"\n"}),write_stdin:async args=>{assert.equal(JSON.parse(args.chars).cancelled,true);return {exit_code:0,output:JSON.stringify({type:"summary",status:"paused",reason:"host_cancelled"})+"\n"}}};
const result=await bridge.run({tools,bindings,provider:"gmail",script:"/synthetic/worker",stateDir:"/synthetic/state",runId,approvedRunId:runId,cancelled:()=>true});assert.equal(calls,0);assert.equal(result.reason,"host_cancelled");
''')


    def test_freeze_mode_never_dispatches_a_write_request(self):
        self.check(r'''
let calls=0;const bindings=Object.fromEntries(["gmail.profile","gmail.labels","gmail.search"].map(key=>[key,async()=>{calls++;return {}}]));
const tools={exec_command:async()=>({session_id:1,output:JSON.stringify({type:"request",id:1,operation:"gmail.modify",arguments:{}})+"\n"}),write_stdin:async args=>{assert.equal(JSON.parse(args.chars).cancelled,true);return {exit_code:0,output:""}}};
await assert.rejects(bridge.run({tools,bindings,provider:"gmail",mode:"freeze",inputPath:"/synthetic/input",script:"/synthetic/script",stateDir:"/synthetic/state"}));assert.equal(calls,0);
''')


if __name__ == "__main__":
    unittest.main()
