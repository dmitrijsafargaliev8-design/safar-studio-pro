"""Exercise the actual ASGI application and contracts without starting a GPU."""
import os
import unittest
import warnings
from unittest.mock import patch
from uuid import uuid4
from fastapi.testclient import TestClient
import safar_modal_server as service
from engine.contracts import Job


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{"SAFAR_GPU_TOKEN":"local-contract-fixture"});self.env.start();self.addCleanup(self.env.stop)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore");self.client=TestClient(service.api.local())
        self.auth={"Authorization":"Bearer local-contract-fixture"}
    def test_health_and_auth(self):
        self.assertEqual(self.client.get("/health").status_code,401)
        self.assertEqual(self.client.get("/health",headers=self.auth).json()["version"],"5.0.0")
        for route in ["/api/generate","/api/repaint","/api/lora/train","/api/export/sync-pack"]:
            self.assertIn(route,self.client.get("/openapi.json").json()["paths"])
    def test_scoped_file_ticket(self):
        owner=str(uuid4());id=str(uuid4())
        r=self.client.post("/api/tickets",headers=self.auth,json={"owner":owner,"job_id":id,"file":"Master.wav"})
        self.assertEqual(r.status_code,200)
        item=r.json();claims=service.decode_ticket(item["path"].split("/")[-1])
        self.assertEqual((claims["owner"],claims["id"],claims["file"],claims["op"]),(owner,id,"Master.wav","read"))
        self.assertEqual(self.client.put(item["path"],content=b"x").status_code,403)
        self.assertEqual(self.client.post("/api/tickets",headers=self.auth,json={"owner":owner,"job_id":id,"file":"notes.txt"}).status_code,422)
    def test_contracts(self):
        with self.assertRaises(ValueError):Job(start_sec=1,end_sec=0).check_task("repaint")
        with self.assertRaises(ValueError):Job(upload_ids=[uuid4()]*5,rights_confirmed=True).check_task("train")
        Job(base_wav_b64="UklGRg==",start_sec=0,end_sec=1).check_task("repaint")
        j=str(uuid4())
        with patch.object(service,"dispatch",return_value=j):
            for route in ("/api/generate","/api/repaint","/api/jobs","/api/lora/train"):
                r=self.client.post(route,headers={**self.auth,"Prefer":"respond-async"},json={"brief":"test"});self.assertEqual(r.status_code,202);self.assertEqual(r.json()["job_id"],j)
if __name__=="__main__":unittest.main()
