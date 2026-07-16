from __future__ import annotations
import argparse,csv,html,json,shutil
from datetime import datetime
from pathlib import Path
import sys

# Permit direct execution by absolute file path from any SLURM working directory.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image
from pipeline.run_metadata import RunMetadata

def safe(s): return ''.join(c if c.isalnum() or c in '._-' else '_' for c in str(s))
def thumb(src,dst):
 dst.parent.mkdir(parents=True,exist_ok=True)
 with Image.open(src) as im:
  converted=im.convert('RGB'); converted.thumbnail((1600,1600),Image.Resampling.LANCZOS); converted.save(dst,'PNG',optimize=True)
def append_csv(src,dst,metadata):
 rows=list(csv.DictReader(src.open(encoding='utf-8-sig')))
 if not rows:return
 fields=list(metadata.csv_columns())+list(rows[0])
 exists=dst.exists()
 with dst.open('a',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');
  if not exists:w.writeheader()
  for r in rows:w.writerow({**metadata.csv_columns(),**r})
def build_html(qc):
 rows=[]
 status=qc/'run_status.csv'
 if status.exists(): rows=list(csv.DictReader(status.open()))
 cards=[]
 for r in rows:
  img=r.get('track_map',''); cls='pass' if r.get('status')=='PASS' else 'fail'
  cards.append(f'<article class="{cls}"><a href="{html.escape(img)}"><img src="{html.escape(img)}"></a><h3>{html.escape(r.get("video",""))} · {html.escape(r.get("cell",""))}</h3><p>{html.escape(r.get("status",""))} — {html.escape(r.get("notes",""))}</p><a href="{html.escape(r.get("run_dir",""))}">Original run</a></article>')
 (qc/'QC_Report.html').write_text('<!doctype html><meta charset="utf-8"><title>Pipeline QC</title><style>body{font-family:Arial;margin:24px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}article{border:3px solid #999;padding:10px;border-radius:10px}.pass{border-color:#26844a}.fail{border-color:#b33}img{width:100%;height:220px;object-fit:contain;background:#eee}</style><h1>IDtracker Pipeline QC</h1><div class="grid">'+''.join(cards)+'</div>',encoding='utf-8')
def collect(run_dir,project_root,metadata):
 qc=project_root/'QC'; tracks=qc/'track_maps'; qc.mkdir(parents=True,exist_ok=True); tracks.mkdir(exist_ok=True)
 status='PASS'; notes=[]
 if not (run_dir/'status/postprocess.txt').exists(): status='FAIL'; notes.append('postprocess status missing')
 pngs=[p for p in run_dir.rglob('*.png') if 'track' in p.name.lower() or 'trajectory' in p.name.lower()]
 name=f'{status}_{safe(Path(metadata.video_filename).stem)}_{safe(metadata.cell_label)}_{metadata.run_index:05d}.png'; rel=''
 if pngs: thumb(pngs[0],tracks/name); rel=f'track_maps/{name}'
 else: status='FAIL'; notes.append('track map missing')
 mapping={'fight_summary':'fight_summary_all.csv','fight_individual':'fight_individuals_all.csv','ba_summary':'BA_summary_all.csv','ba_individual':'BA_individuals_all.csv'}
 for src in run_dir.rglob('*.csv'):
  low=src.name.lower()
  for key,out in mapping.items():
   if key in low: append_csv(src,qc/out,metadata); break
 index=qc/'run_status.csv'; fields=['run_index','video','cell','analysis','status','tracking','archive','post','qc','track_map','run_dir','notes','collected_at']; exists=index.exists()
 def readstat(n):
  p=run_dir/'status'/n; return p.read_text().strip() if p.exists() else ''
 with index.open('a',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fields); 
  if not exists:w.writeheader()
  w.writerow({'run_index':metadata.run_index,'video':metadata.video_filename,'cell':metadata.cell_label,'analysis':metadata.analysis_type,'status':status,'tracking':readstat('tracking.txt'),'archive':readstat('archive.txt'),'post':readstat('postprocess.txt'),'qc':'PASS' if pngs else 'FAIL','track_map':rel,'run_dir':str(run_dir),'notes':'; '.join(notes),'collected_at':datetime.now().isoformat(timespec='seconds')})
 build_html(qc); (run_dir/'status/collector.txt').write_text('PASS\n'); (run_dir/'status/stage.txt').write_text('Complete\n')
 return {'status':status,'track_maps':len(pngs)}
def main():
 p=argparse.ArgumentParser(); p.add_argument('--run-dir',required=True);p.add_argument('--project-root',required=True);p.add_argument('--run-metadata-json',required=True);a=p.parse_args();print(collect(Path(a.run_dir),Path(a.project_root),RunMetadata.from_json(a.run_metadata_json)))
if __name__=='__main__':main()
