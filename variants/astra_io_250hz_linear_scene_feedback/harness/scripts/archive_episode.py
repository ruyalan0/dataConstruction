"""Archive a completed episode and verify its bytes after HDFS round trip."""
import argparse,hashlib,json,subprocess,tarfile,tempfile
from pathlib import Path

def sha(path):
    with path.open("rb") as stream:return hashlib.file_digest(stream,"sha256").hexdigest()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("run",type=Path)
    p.add_argument("--hdfs-root",default="hdfs://storage.example.invalid/REDACTED")
    a=p.parse_args();run=a.run.resolve()
    results=json.loads((run/"results.json").read_text())
    # A formerly held attempt may have completed a continuation; actual exit is required below.
    for r in results:
        episode=run/("seed-"+str(r["seed_index"]))
        if episode.exists() and not (episode/"exit-status.json").exists():raise RuntimeError("Simulator exit not confirmed")
    archive=run.parent/(run.name+".tar.gz")
    if archive.exists():raise FileExistsError(archive)
    with tarfile.open(archive,"w:gz") as target:target.add(run,arcname=run.name)
    expected=sha(archive)
    uri=a.hdfs_root+"/"+archive.name
    subprocess.run(["hdfs","dfs","-mkdir","-p",a.hdfs_root],check=True)
    subprocess.run(["hdfs","dfs","-put",str(archive),uri],check=True)
    with tempfile.TemporaryDirectory(prefix="rsi-verify-") as directory:
        copy=Path(directory)/archive.name
        subprocess.run(["hdfs","dfs","-get",uri,str(copy)],check=True)
        if sha(copy)!=expected:raise RuntimeError("HDFS readback checksum mismatch")
    receipt={"uri":uri,"sha256":expected,"bytes":archive.stat().st_size,"verified_readback":True}
    archive.with_suffix(".receipt.json").write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt))

if __name__=="__main__":main()
