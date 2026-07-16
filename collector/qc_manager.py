from __future__ import annotations
import argparse,csv,sys
from pathlib import Path
REPO_ROOT=Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:sys.path.insert(0,str(REPO_ROOT))

def read(path): return list(csv.DictReader(path.open(encoding='utf-8-sig'))) if path.exists() else []
QC_FIELDS=['record_id','run_index','video','cell','analysis','pipeline_status','qc_decision','track_preview','track_pdf','run_dir','notes','collected_at']

def normalize_qc_row(raw):
 row={key:'' for key in QC_FIELDS}
 for key in QC_FIELDS:
  if key in raw and raw.get(key) is not None: row[key]=raw.get(key,'')
 row['pipeline_status']=row['pipeline_status'] or raw.get('status','') or raw.get('post','')
 row['qc_decision']=row['qc_decision'] or raw.get('qc','') or 'PENDING'
 row['track_preview']=row['track_preview'] or raw.get('track_map','')
 return row

def write(path,rows,fields):
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_suffix(path.suffix+'.tmp')
 with tmp.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
 tmp.replace(path)
def rebuild(root):
 qc=root/'QC'; rows=[normalize_qc_row(r) for r in read(qc/'run_status.csv')]; outdir=qc/'master_summaries';outdir.mkdir(parents=True,exist_ok=True)
 counts={}
 for analysis,filename,candidates in [
  ('fight','master_fight_individual_summaries.csv',['combat_individual_summary_all.csv','fight_individuals_all.csv']),
  ('ba','master_BA_individual_summaries.csv',['ba_individual_summary_all.csv','BA_individuals_all.csv'])]:
  combined=[]
  for rec in rows:
   if rec.get('qc_decision')!='DONE' or rec.get('analysis')!=analysis:continue
   run=Path(rec['run_dir']);src=None
   for name in candidates:
    p=run/'outputs'/name
    if p.exists():src=p;break
   if not src:continue
   for n,row in enumerate(read(src),1):combined.append({'qc_record_id':rec['record_id'],'qc_decision':'DONE','qc_source_file':str(src),'qc_source_row':n,**row})
  target=outdir/filename
  if combined:write(target,combined,list(combined[0]))
  else:write(target,[],['qc_record_id','qc_decision','qc_source_file','qc_source_row'])
  counts[analysis]=len(combined)
 return counts
def set_status(root,rid,decision,notes=''):
 path=root/'QC'/'run_status.csv';rows=[normalize_qc_row(r) for r in read(path)]
 found=False
 for r in rows:
  if r.get('record_id')==rid:r['qc_decision']=decision;r['notes']=notes or r.get('notes','');found=True
 if not found:raise SystemExit(f'Unknown record ID: {rid}')
 write(path,rows,QC_FIELDS);return rebuild(root)
def migrate_index(root):
 path=root/'QC'/'run_status.csv'
 rows=[normalize_qc_row(r) for r in read(path)]
 if rows or path.exists(): write(path,rows,QC_FIELDS)
 return {'migrated_rows':len(rows),'path':str(path)}

def main():
 p=argparse.ArgumentParser();p.add_argument('--project-root',required=True);p.add_argument('--record-id');p.add_argument('--decision',choices=['PENDING','DONE','RERUN']);p.add_argument('--notes',default='');p.add_argument('--rebuild',action='store_true');p.add_argument('--migrate-index',action='store_true');a=p.parse_args();root=Path(a.project_root)
 if a.migrate_index: print(migrate_index(root))
 elif a.rebuild: print(rebuild(root))
 else: print(set_status(root,a.record_id,a.decision,a.notes))
if __name__=='__main__':main()
