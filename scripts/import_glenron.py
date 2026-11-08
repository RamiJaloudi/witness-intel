"""Import only the generated evidence into Glenron. Never sends data to an AI provider."""
from pathlib import Path
import json
import httpx

def main():
    root=Path(__file__).resolve().parents[1]/'data'/'glenron'
    manifest=json.loads((root/'benchmark_DO_NOT_IMPORT/manifest.json').read_text())
    expected={r['sha256'] for r in manifest['files']}
    companion=Path(__file__).resolve().parents[1]/'datasets/enron/benchmark_DO_NOT_IMPORT/manifest.json'
    enron={r['sha256'] for r in json.loads(companion.read_text())['files']} if companion.exists() else set()
    with httpx.Client(base_url='http://127.0.0.1:8765/api',timeout=120,trust_env=False) as client:
        def request(method,path,**kwargs):
            response=client.request(method,path,**kwargs);response.raise_for_status();return response.json()
        matches=[m for m in request('GET','/matters') if m['name']=='Glenron']
        if len(matches)>1:raise SystemExit('More than one Glenron workspace exists; choose the intended workspace before importing.')
        if matches:m=matches[0]
        else:m=request('POST','/matters',json={'name':'Glenron','privacy_mode':'mask','instructions':'Fictional GLENRON-SYNTH-1.0 corpus. 500 evidence documents. Benchmark answer key is not evidence and must not be imported.'})
        mid=m['id'];existing=request('GET',f'/matters/{mid}/workspace?include_text=false')
        if any(d['sha256'] not in expected|enron for d in existing['documents']):raise SystemExit('Existing Glenron contains other evidence; no files were added.')
        for i,record in enumerate(manifest['files'],1):
            result=request('POST',f'/matters/{mid}/folder/file',json={'path':str((root/'documents').resolve()),'relative_path':record['file']})
            if result.get('status')=='failed':raise SystemExit(f'Extraction failed for {record["document_id"]}; inspect the inventory.')
            if i%50==0:print(f'Processed {i}/500 evidence documents',flush=True)
        workspace=request('GET',f'/matters/{mid}/workspace?include_text=false')
        synthetic=[d for d in workspace['documents'] if d['sha256'] in expected]
        assert len(synthetic)==500
        assert all(d['status']!='failed' for d in workspace['documents'])
        assert {d['sha256'] for d in synthetic}==expected
        report={'workspace':'Glenron','workspace_id':mid,'documents':len(workspace['documents']),'passages':len(workspace['chunks']),'failed_documents':0,'benchmark_files_imported':False,'analysis_runs':len(workspace['runs']),'ai_called_by_import':False}
        report['synthetic_documents']=len(synthetic)
        report['enron_documents']=sum(d['sha256'] in enron for d in workspace['documents'])
        (root/'benchmark_DO_NOT_IMPORT/import_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report,indent=2),flush=True)

if __name__=='__main__':main()
