#!/usr/bin/env python3
import base64, json, os, re, subprocess, shutil, hashlib
from pathlib import Path
from urllib.parse import quote, urlparse
import requests, yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
APPS_FILE = ROOT / "apps.json"
REPO_DIR = ROOT / "repo"
METADATA_DIR = ROOT / "metadata"
STATE_FILE = ROOT / "build-state.json"
REPO_DIR.mkdir(parents=True, exist_ok=True)
METADATA_DIR.mkdir(parents=True, exist_ok=True)

TOKEN = os.environ.get("GH_TOKEN", "")
HEADERS = {"Accept":"application/vnd.github+json","User-Agent":"Vinicius-Tutoriais-Apps"}
if TOKEN: HEADERS["Authorization"] = f"Bearer {TOKEN}"
SESSION = requests.Session(); SESSION.headers.update(HEADERS)

def gh_get(url):
    r = SESSION.get(url, timeout=45); r.raise_for_status(); return r.json()

def release_for(owner, project, app):
    if app.get("release_tag"):
        tag = quote(str(app["release_tag"]), safe="")
        return gh_get(f"https://api.github.com/repos/{owner}/{project}/releases/tags/{tag}")
    if app.get("include_prerelease"):
        releases = gh_get(f"https://api.github.com/repos/{owner}/{project}/releases?per_page=100")
        releases = [x for x in releases if not x.get("draft")]
        if not releases: raise RuntimeError(f"{owner}/{project}: nenhuma release publicada.")
        releases.sort(key=lambda x:x.get("published_at") or x.get("created_at") or "", reverse=True)
        return releases[0]
    return gh_get(f"https://api.github.com/repos/{owner}/{project}/releases/latest")

def clean_markdown(text, max_chars=1800):
    if not text: return ""
    text = re.sub(r"~~~.*?~~~", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"<img[^>]*>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"[*_~]+", "", text)
    lines=[]
    for line in text.splitlines():
        s=line.strip()
        if not s: lines.append(""); continue
        low=s.lower()
        if "shields.io" in low or "badge" in low or s.startswith("[!["): continue
        if s.startswith("|") and s.endswith("|"): continue
        lines.append(s)
    text="\n".join(lines)
    text=re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text)>max_chars: text=text[:max_chars].rsplit(" ",1)[0].rstrip()+"…"
    return text

def read_repo_readme(owner, project):
    try:
        data=gh_get(f"https://api.github.com/repos/{owner}/{project}/readme")
        if data.get("encoding")=="base64" and data.get("content"):
            raw=base64.b64decode(data["content"]).decode("utf-8","replace")
            return clean_markdown(raw)
    except Exception as exc: print(f"::warning::README {owner}/{project}: {exc}")
    return ""

def parse_badging(apk):
    proc=subprocess.run(["aapt","dump","badging",str(apk)],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=True)
    out=proc.stdout
    def first(pattern, default=""):
        m=re.search(pattern,out,flags=re.M); return m.group(1) if m else default
    package=first(r"^package:\s+name='([^']+)'")
    version_code=first(r"^package:.*versionCode='([^']+)'")
    version_name=first(r"^package:.*versionName='([^']*)'")
    min_sdk=first(r"^sdkVersion:'([^']+)'")
    target_sdk=first(r"^targetSdkVersion:'([^']+)'")
    label=first(r"^application-label(?:-[^:]+)?:'([^']*)'")
    icon=first(r"^application:.*icon='([^']*)'")
    native_line=first(r"^native-code:\s*(.+)$")
    abis=re.findall(r"'([^']+)'",native_line) if native_line else []
    if not package: raise RuntimeError(f"Não foi possível ler packageName de {apk.name}")
    return {"package":package,"versionCode":int(version_code) if str(version_code).isdigit() else 0,
            "versionName":version_name or "desconhecida","minSdk":min_sdk or "não informado",
            "targetSdk":target_sdk or "não informado","label":label,"iconRef":icon,"abis":abis}

def normalize_http_url(value):
    value=(value or "").strip()
    if not value: return ""
    if re.match(r"^https?://", value, flags=re.I): return value
    if value.startswith("//"): return "https:" + value
    if re.match(r"^[A-Za-z0-9.-]+(?::[0-9]+)?(?:/.*)?$", value):
        return "https://" + value
    return ""

def detect_category(app_name, summary):
    t=f"{app_name} {summary}".lower()
    if any(k in t for k in ["emulator","emulador","winlator","vita3k","x360","bachata","emucore","winnative","xenra","lastwave"]): return "Games"
    if any(k in t for k in ["browser","newpipe","dns","network"]): return "Internet"
    if any(k in t for k in ["image","denois","recorder","audio","video"]): return "Multimedia"
    return "System"

def make_fallback_icon(path, name):
    path.parent.mkdir(parents=True, exist_ok=True)
    img=Image.new("RGB",(512,512),(36,39,44)); draw=ImageDraw.Draw(img)
    initials="".join(x[0] for x in re.findall(r"[A-Za-z0-9]+",name)[:2]).upper() or "APP"
    try:
        font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",150)
        small=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",34)
    except Exception:
        font=ImageFont.load_default(); small=ImageFont.load_default()
    bbox=draw.textbbox((0,0),initials,font=font); x=(512-(bbox[2]-bbox[0]))/2
    draw.text((x,155),initials,fill=(245,245,245),font=font)
    draw.text((256,430),"Vinicius Tutoriais Apps",anchor="mm",fill=(180,180,180),font=small)
    img.save(path,"PNG",optimize=True)

def write_metadata(app, repo_info, release, apk_path, tech, readme):
    package=tech["package"]; app_name=app.get("display_name") or tech.get("label") or app["name"]
    summary=(app.get("summary") or repo_info.get("description") or f"{app_name} — aplicativo selecionado pelo Vinicius Tutoriais.").strip()
    summary=re.sub(r"\s+"," ",summary)[:80].rstrip()
    description=clean_markdown(app.get("description") or readme or repo_info.get("description") or summary,1800)
    size_mb=apk_path.stat().st_size/(1024*1024)
    abis=", ".join(tech["abis"]) if tech["abis"] else "não informado"
    release_url=release.get("html_url") or f'{app["source"].rstrip("/")}/releases'
    technical=("\n\nInformações técnicas\n"
      f"Versão: {tech['versionName']}\nVersion code: {tech['versionCode']}\n"
      f"Android mínimo (SDK): {tech['minSdk']}\nTarget SDK: {tech['targetSdk']}\n"
      f"Arquiteturas: {abis}\nTamanho do APK: {size_mb:.1f} MB\n"
      f"Release oficial: {release_url}\nCódigo-fonte: {app['source']}")
    homepage=normalize_http_url(repo_info.get("homepage"))
    license_id=((repo_info.get("license") or {}).get("spdx_id") or "").strip()
    if license_id in {"NOASSERTION","OTHER"}: license_id=""
    meta={"AutoName":app_name,"Summary":summary,"Description":description+technical,
          "Categories":[app.get("category") or detect_category(app_name,summary)],
          "WebSite":homepage,"SourceCode":app["source"],
          "IssueTracker":f'{app["source"].rstrip("/")}/issues' if repo_info.get("has_issues") else "",
          "Changelog":f'{app["source"].rstrip("/")}/releases',
          "CurrentVersion":tech["versionName"],"CurrentVersionCode":tech["versionCode"]}
    if license_id: meta["License"]=license_id
    meta={k:v for k,v in meta.items() if v not in ("",None,[])}
    (METADATA_DIR/f"{package}.yml").write_text(yaml.safe_dump(meta,allow_unicode=True,sort_keys=False,width=1000),encoding="utf-8")
    if not tech.get("iconRef"):
        make_fallback_icon(METADATA_DIR/package/"pt-BR"/"images"/"icon.png",app_name)
    locale_dir=METADATA_DIR/package/"pt-BR"; locale_dir.mkdir(parents=True,exist_ok=True)
    (locale_dir/"short_description.txt").write_text(summary+"\n",encoding="utf-8")
    (locale_dir/"full_description.txt").write_text(description+technical+"\n",encoding="utf-8")
    return {"name":app_name,"package":package,"versionName":tech["versionName"],"versionCode":tech["versionCode"],
            "release":release.get("tag_name"),"apk":apk_path.name,"source":app["source"],"summary":summary,
            "minSdk":tech["minSdk"],"targetSdk":tech["targetSdk"],"abis":tech["abis"],"sizeMB":round(size_mb,1)}

def main():
    apps=json.loads(APPS_FILE.read_text(encoding="utf-8"))
    for p in REPO_DIR.glob("*.apk"): p.unlink()
    if METADATA_DIR.exists():
        for p in list(METADATA_DIR.iterdir()):
            p.unlink() if p.is_file() else shutil.rmtree(p)
    states=[]; seen_packages={}
    for app in apps:
        if app.get("enabled",True) is False:
            print(f"[{app.get('name','?')}] desativado; ignorando."); continue
        name=app["name"]; source=app["source"]; parsed=urlparse(source)
        if parsed.netloc.lower()!="github.com": raise SystemExit(f"{name}: source precisa ser um repositório GitHub.")
        parts=[p for p in parsed.path.strip("/").split("/") if p]
        if len(parts)<2: raise SystemExit(f"{name}: link GitHub inválido: {source}")
        owner,project=parts[:2]
        repo_info=gh_get(f"https://api.github.com/repos/{owner}/{project}")
        release=release_for(owner,project,app); pattern=re.compile(app.get("asset_regex",r"\.apk$"),re.I)
        matches=[asset for asset in release.get("assets",[]) if pattern.search(asset.get("name",""))]
        if not matches:
            names=", ".join(a.get("name","") for a in release.get("assets",[]))
            raise SystemExit(f"{name}: nenhum APK corresponde a {pattern.pattern!r}. Arquivos disponíveis: {names}")
        matches.sort(key=lambda a:("universal" not in a["name"].lower(),"arm64" not in a["name"].lower(),a["name"].lower()))
        asset=matches[0]; safe=re.sub(r"[^A-Za-z0-9._-]+","-",name).strip("-")
        dest=REPO_DIR/f"{safe}__{asset['name']}"
        print(f"\n[{name}] release {release.get('tag_name')} ({'pre-release' if release.get('prerelease') else 'estável'})")
        with SESSION.get(asset["browser_download_url"],stream=True,timeout=300,allow_redirects=True) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=1024*1024):
                    if chunk: f.write(chunk)
        expected_digest=(asset.get("digest") or "").strip()
        if name == "DroidDeck" and not expected_digest.startswith("sha256:"):
            dest.unlink(missing_ok=True)
            raise SystemExit("DroidDeck: release oficial sem digest SHA-256; publicação interrompida.")

        if expected_digest.startswith("sha256:"):
            h=hashlib.sha256()
            with dest.open("rb") as f:
                for chunk in iter(lambda: f.read(1024*1024), b""):
                    h.update(chunk)
            actual_digest="sha256:" + h.hexdigest()
            if actual_digest.lower() != expected_digest.lower():
                dest.unlink(missing_ok=True)
                raise SystemExit(
                    f"{name}: SHA-256 do APK baixado não confere com o digest oficial do GitHub."
                )
            print(f"[{name}] SHA-256 oficial conferido.")

        if name == "DroidDeck":
            verify=subprocess.run(
                ["apksigner","verify","--verbose",str(dest)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if verify.returncode != 0:
                dest.unlink(missing_ok=True)
                raise SystemExit("DroidDeck: apksigner rejeitou o APK oficial.\n" + verify.stdout)
            print("[DroidDeck] assinatura APK validada pelo apksigner.")

        tech=parse_badging(dest)
        if tech["package"] in seen_packages:
            print(f"::warning::{name} usa o mesmo packageName de {seen_packages[tech['package']]}: {tech['package']}")
        else: seen_packages[tech["package"]]=name
        state=write_metadata(app,repo_info,release,dest,tech,read_repo_readme(owner,project)); states.append(state)
        print(f"[{name}] {tech['package']} | {tech['versionName']} ({tech['versionCode']}) | {dest.stat().st_size/1024/1024:.1f} MB")
    STATE_FILE.write_text(json.dumps(states,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"\nMetadados gerados para {len(states)} aplicativos.")

if __name__=="__main__": main()