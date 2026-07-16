from __future__ import annotations
import argparse,csv,html,shutil
from datetime import datetime
from pathlib import Path
import sys
REPO_ROOT=Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path: sys.path.insert(0,str(REPO_ROOT))
from PIL import Image
from pipeline.run_metadata import RunMetadata

def safe(s): return ''.join(c if c.isalnum() or c in '._-' else '_' for c in str(s))
def thumb(src,dst):
 dst.parent.mkdir(parents=True,exist_ok=True)
 with Image.open(src) as im:
  converted=im.convert('RGB'); converted.thumbnail((1600,1600),Image.Resampling.LANCZOS); converted.save(dst,'PNG',optimize=True)
def pdf_from_pngs(pngs,dst):
 pages=[]
 for p in pngs:
  try:
   with Image.open(p) as im: pages.append(im.convert('RGB').copy())
  except Exception: pass
 if not pages:return False
 dst.parent.mkdir(parents=True,exist_ok=True)
 pages[0].save(dst,'PDF',save_all=True,append_images=pages[1:],resolution=150.0)
 return True
def canonical_track_pngs(outputs):
 """Return one copy of each logical track map, avoiding all_track_maps duplicates."""
 candidates=sorted(p for p in outputs.rglob('*.png') if 'track_map' in p.name.lower() or 'trajectory' in p.name.lower())
 chosen={}
 for p in candidates:
  name=p.name.lower()
  if 'animal0' in name: key='animal0'
  elif 'animal1' in name: key='animal1'
  elif 'animal2' in name: key='animal2'
  else: key='combined'
  # Prefer the detailed per-session source over the duplicated all_track_maps copy.
  score=(1 if 'all_track_maps' not in p.parts else 0, -len(str(p)))
  if key not in chosen or score>chosen[key][0]: chosen[key]=(score,p)
 order=['combined','animal0','animal1','animal2']
 return [chosen[k][1] for k in order if k in chosen]+[v[1] for k,v in sorted(chosen.items()) if k not in order]
def build_qc_bundle(outputs,rid,analysis,pngs,fight_pdf=None):
 bundle=outputs/'QC_review_bundle'
 if bundle.exists(): shutil.rmtree(bundle)
 bundle.mkdir(parents=True,exist_ok=True)
 copied=[]
 summaries=sorted(p for p in outputs.rglob('*.csv') if 'individual_summary' in p.name.lower() and 'QC_review_bundle' not in p.parts and not p.name.startswith(rid+'__'))
 seen=set()
 for src in summaries:
  rel=src.relative_to(outputs)
  # Keep both aggregate and per-session files, but flatten safely and identify source.
  stem='__'.join(rel.parts)
  name=f'{rid}__{safe(stem)}'
  if name in seen: continue
  seen.add(name); dst=bundle/name; shutil.copy2(src,dst); copied.append(dst.name)
 if analysis=='fight' and fight_pdf and fight_pdf.exists():
  dst=bundle/f'{rid}_tracks.pdf'; shutil.copy2(fight_pdf,dst); copied.append(dst.name)
 elif analysis=='ba':
  for i,src in enumerate(pngs,1):
   dst=bundle/f'{rid}_track_{i:02d}_{safe(src.name)}'; shutil.copy2(src,dst); copied.append(dst.name)
 (bundle/'QC_FILE_INDEX.txt').write_text('Record ID: '+rid+'\nAnalysis: '+analysis+'\n\nFiles:\n'+'\n'.join(copied)+'\n',encoding='utf-8')
 return bundle
def build_html(qc):
 rows=[]; status=qc/'run_status.csv'
 if status.exists(): rows=list(csv.DictReader(status.open(encoding='utf-8-sig')))
 cards=[]
 for r in rows:
  img=r.get('track_preview',''); dec=r.get('qc_decision','PENDING')
  cards.append(f'<article><a href="{html.escape(img)}"><img src="{html.escape(img)}"></a><h3>{html.escape(r.get("record_id",""))}</h3><p>{html.escape(r.get("video",""))} · {html.escape(r.get("cell",""))}</p><strong>{html.escape(dec)}</strong><p>{html.escape(r.get("notes",""))}</p></article>')
 (qc/'QC_Report.html').write_text('<!doctype html><meta charset="utf-8"><title>Pipeline QC</title><style>body{font-family:Arial;margin:24px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}article{border:2px solid #999;padding:10px;border-radius:10px}img{width:100%;height:240px;object-fit:contain;background:#eee}</style><h1>IDtracker Pipeline QC</h1><p>Use the Mac GUI QC tab to mark runs DONE or RERUN.</p><div class="grid">'+''.join(cards)+'</div>',encoding='utf-8')
QC_FIELDS=['record_id','run_index','video','cell','analysis','pipeline_status','qc_decision','track_preview','track_pdf','run_dir','notes','date_run','collected_at','replaces','replaced_by']
def normalize_status_row(raw):
 row={key:'' for key in QC_FIELDS}
 for key in QC_FIELDS:
  if key in raw and raw.get(key) is not None: row[key]=raw.get(key,'')
 row['pipeline_status']=row['pipeline_status'] or raw.get('status','') or raw.get('post','')
 row['qc_decision']=(row['qc_decision'] or raw.get('qc','') or 'PENDING').upper()
 if row['qc_decision']=='FIXED': row['qc_decision']='SUPERSEDED'
 row['track_preview']=row['track_preview'] or raw.get('track_map','')
 if not row['date_run']:
  row['date_run']=raw.get('run_timestamp','') or raw.get('collected_at','')
  if not row['date_run']:
   rid=row.get('record_id','')
   stamp=rid.rsplit('_',1)[-1] if '_' in rid else ''
   if len(stamp)==15 and stamp[8]=='T' and stamp.replace('T','').isdigit():
    row['date_run']=f'{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}'
 legacy=[]
 for key in ('tracking','post','archive','status'):
  value=raw.get(key,'')
  if value: legacy.append(f'{key}={value}')
 if legacy and not row['notes']: row['notes']='; '.join(legacy)
 return row
def upsert_status(index,row):
 rows=[]
 if index.exists():
  with index.open(encoding='utf-8-sig',newline='') as f: rows=[normalize_status_row(r) for r in csv.DictReader(f)]
 normalized=normalize_status_row(row); rows=[r for r in rows if r.get('record_id')!=normalized['record_id']]+[normalized]
 index.parent.mkdir(parents=True,exist_ok=True); tmp=index.with_suffix(index.suffix+'.tmp')
 with tmp.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=QC_FIELDS,extrasaction='ignore');w.writeheader();w.writerows(rows)
 tmp.replace(index)
def collect(run_dir,project_root,metadata):
 qc=project_root/'QC'; previews=qc/'track_previews'; pdfs=qc/'fight_track_pdfs'; qc.mkdir(parents=True,exist_ok=True)
 rid=metadata.identifier(); outputs=run_dir/'outputs'; status='PASS';notes=[]
 if not (run_dir/'status/postprocess.txt').exists():status='FAIL';notes.append('postprocess status missing')
 pngs=canonical_track_pngs(outputs); preview_rel='';pdf_rel=''; fight_pdf=None
 if pngs:
  preview=previews/f'{rid}_preview.png';thumb(pngs[0],preview);preview_rel=f'track_previews/{preview.name}'
 else: status='FAIL';notes.append('track map missing')
 if metadata.analysis_type=='fight' and pngs:
  fight_pdf=pdfs/f'{rid}_tracks.pdf'
  if pdf_from_pngs(pngs,fight_pdf):
   pdf_rel=f'fight_track_pdfs/{fight_pdf.name}';shutil.copy2(fight_pdf,outputs/f'{rid}_tracks.pdf')
 elif metadata.analysis_type=='ba':
  ba_dir=qc/'ba_track_pngs'/rid;ba_dir.mkdir(parents=True,exist_ok=True)
  for i,p in enumerate(pngs,1):shutil.copy2(p,ba_dir/f'{rid}_track_{i:02d}.png')
 for src in list(outputs.glob('*.csv')):
  if any(x in src.name.lower() for x in ('summary','manifest')):
   tagged=outputs/f'{rid}__{src.name}'
   if tagged!=src and not tagged.exists():shutil.copy2(src,tagged)
 bundle=build_qc_bundle(outputs,rid,metadata.analysis_type,pngs,fight_pdf)
 index=qc/'run_status.csv'; previous='PENDING'
 if index.exists():
  for r in csv.DictReader(index.open(encoding='utf-8-sig')):
   if r.get('record_id')==rid: previous=r.get('qc_decision') or 'PENDING'
 upsert_status(index,{'record_id':rid,'run_index':metadata.run_index,'video':metadata.video_filename,'cell':metadata.cell_label,'analysis':metadata.analysis_type,'pipeline_status':status,'qc_decision':previous,'track_preview':preview_rel,'track_pdf':pdf_rel,'run_dir':str(run_dir),'notes':'; '.join(notes),'date_run':metadata.run_timestamp,'collected_at':datetime.now().isoformat(timespec='seconds')})
 build_html(qc);(run_dir/'status/collector.txt').write_text('PASS\n');(run_dir/'status/stage.txt').write_text('Complete\n')
 return {'status':status,'record_id':rid,'track_maps':len(pngs),'fight_pdf':pdf_rel,'qc_bundle':str(bundle)}
def main():
 p=argparse.ArgumentParser();p.add_argument('--run-dir',required=True);p.add_argument('--project-root',required=True);p.add_argument('--run-metadata-json',required=True);a=p.parse_args();print(collect(Path(a.run_dir),Path(a.project_root),RunMetadata.from_json(a.run_metadata_json)))
if __name__=='__main__':main()
