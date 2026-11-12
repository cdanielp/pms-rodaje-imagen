"""Instalador del pod PMS. Solo se ejecuta en Linux dentro del pod."""
import concurrent.futures
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

# Imagen PMS (ghcr.io/cdanielp/pms-rodaje-h3): ComfyUI, nodos y dependencias ya vienen en /opt/pms.
# Los modelos nunca van en la imagen: /opt/pms/ComfyUI/models apunta a /workspace/pms_models.
IMAGEN = Path('/opt/pms')
MARCA = IMAGEN / 'IMAGEN_PMS.json'
CONSTRUIR = '--imagen' in sys.argv
BASE = IMAGEN / 'ComfyUI' if (CONSTRUIR or MARCA.exists()) else Path('/workspace/runpod-slim/ComfyUI')
PACK = Path(__file__).resolve().parent
TMP = Path(os.environ.get('PMS_TMP', '/workspace/pms_rodaje_tmp'))
MODELOS_IMAGEN = Path('/workspace/pms_models')
MANIFEST = json.loads((PACK / 'modelos.json').read_text())
PY = BASE / '.venv-cu128/bin/python'
STAMP = time.strftime('%Y%m%d_%H%M%S')
LOCK = threading.Lock()
STATUS = {'estado': 'instalando', 'mensaje': 'Preparando el pod', 'eventos': [], 'bytes': 0,
          'comfy_started': '--revisar' in sys.argv,
          'total': sum(m['bytes'] or 160000000 for m in MANIFEST['models'])}

def event(message, kind='OK'):
    line = '[' + kind + '] ' + message
    print(line, flush=True)
    STATUS['mensaje'] = line
    STATUS['eventos'].append(line)
    STATUS['eventos'] = STATUS['eventos'][-60:]
    if kind == 'FALLO':
        STATUS['estado'] = 'fallo'
    publish()

def publish():
    with LOCK:
        TMP.mkdir(parents=True, exist_ok=True)
        STATUS['hora_pod'] = time.strftime('%H:%M:%S')
        pending = TMP / 'progreso.tmp'
        pending.write_text(json.dumps(STATUS, ensure_ascii=False), encoding='utf-8')
        os.replace(pending, TMP / 'progreso.json')

def run(args, label, cwd=None, env=None):
    log = TMP / 'instalacion.log'
    event('En curso: '+label, 'INFO')
    with log.open('a', encoding='utf-8') as out:
        result = subprocess.Popen([str(a) for a in args], cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT)
        while result.poll() is None:
            try:
                result.wait(timeout=20)
            except subprocess.TimeoutExpired:
                lines=log.read_text(errors='replace').splitlines()
                if lines: event(label+': '+lines[-1][-350:], 'INFO')
    if result.returncode:
        tail = '\n'.join(log.read_text(errors='replace').splitlines()[-12:])
        raise RuntimeError(label + ': ' + tail)
    event(label)

def valid(path, model):
    if not path.is_file():
        return False
    if model['bytes'] and path.stat().st_size != model['bytes']:
        return False
    if model['sha256']:
        digest = hashlib.sha256()
        with path.open('rb') as f:
            for block in iter(lambda: f.read(16 * 1024 * 1024), b''):
                digest.update(block)
        return digest.hexdigest() == model['sha256']
    return True

def sellos():
    try:
        return json.loads((TMP / 'verificados.json').read_text())
    except (OSError, ValueError):
        return {}

def sellar(path):
    data = sellos(); st = path.stat()
    data[str(path)] = [st.st_size, st.st_mtime]
    (TMP / 'verificados.json').write_text(json.dumps(data))

def comprobado(path, model):
    st = path.stat() if path.is_file() else None
    return bool(st) and sellos().get(str(path)) == [st.st_size, st.st_mtime] or valid(path, model)

def backup(path):
    if path.exists():
        target = TMP / 'respaldos' / STAMP / path.relative_to(BASE)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, target, dirs_exist_ok=True)
        else:
            shutil.copy2(path, target)
        event('Respaldo: ' + str(path))

def download(model):
    target = BASE / 'models' / model['folder'] / model['file']
    if valid(target, model):
        event('Modelo completo: ' + model['file'])
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    work = TMP / 'descargas' / model['file']
    work.mkdir(parents=True, exist_ok=True)
    repo, path = model['url'].split('huggingface.co/', 1)[1].split('/resolve/main/', 1)
    env = dict(os.environ, HF_XET_HIGH_PERFORMANCE='1', HF_HUB_DISABLE_PROGRESS_BARS='1', HF_HUB_DOWNLOAD_TIMEOUT='60')
    code = 'from huggingface_hub import hf_hub_download; import sys; hf_hub_download(sys.argv[1],sys.argv[2],local_dir=sys.argv[3])'
    logfile = work / 'descarga.log'
    start = time.time()
    with logfile.open('w') as log:
        proc = subprocess.Popen([str(PY), '-c', code, repo, path, str(work)], stdout=log, stderr=subprocess.STDOUT, env=env)
        while proc.poll() is None:
            if time.time() - start > 900:
                proc.kill(); proc.wait()
                break
            time.sleep(1)
    candidate = work / path
    if proc.returncode != 0 or not valid(candidate, model):
        tail = ' '.join(logfile.read_text(errors='replace').splitlines()[-2:])[-400:]
        reason = ('tiempo máximo de 15 min' if time.time() - start > 900 else
                  'descarga incompleta' if proc.returncode == 0 else
                  'el proceso de descarga se cortó (señal ' + str(-proc.returncode) + ')' if proc.returncode < 0 else
                  tail or 'código ' + str(proc.returncode))
        event('Modo rápido falló en ' + model['file'] + ': ' + reason + '. Cambio automático a modo compatible (curl).', 'INFO')
        candidate = work / 'compatible.part'
        run(['curl', '--fail', '-L', '--retry', '3', '--retry-all-errors', '--connect-timeout', '30', '--speed-limit', '1048576',
             '--speed-time', '60', '--max-time', '1200', '-o', candidate, model['url']], 'Descarga compatible: ' + model['file'])
    if not valid(candidate, model):
        raise RuntimeError('Tamaño o SHA256 incorrecto: ' + model['file'])
    backup(target)
    # Solo archivos temporales propios: se publica el archivo ya comprobado.
    os.replace(candidate, target)
    sellar(target)
    event('Modelo completo: ' + model['file'])

def progress():
    total = 0
    for m in MANIFEST['models']:
        target = BASE / 'models' / m['folder'] / m['file']
        expected = m['bytes'] or 160000000
        if target.is_file():
            total += min(target.stat().st_size, expected)
        else:
            directory = TMP / 'descargas' / m['file']
            size = sum(f.stat().st_size for f in directory.rglob('*') if f.is_file() and f.suffix != '.log') if directory.exists() else 0
            total += min(size, expected)
    STATUS['bytes'] = total
    publish()

def inspect():
    def get(path):
        with urllib.request.urlopen('http://127.0.0.1:8188' + path, timeout=30) as r:
            return json.load(r)
    st = get('/system_stats')
    if st.get('system', {}).get('comfyui_version') != '0.44.2':
        raise RuntimeError('ComfyUI no está en 0.44.2')
    event('ComfyUI 0.44.2 responde')
    info = get('/object_info')
    for workflow in (PACK / 'workflows').glob('*.json'):
        data = json.loads(workflow.read_text(encoding='utf-8'))
        subgraphs = (data.get('definitions') or {}).get('subgraphs', [])
        virtual = {'MarkdownNote', 'Note', 'Reroute', 'PrimitiveNode'} | {sg['id'] for sg in subgraphs}
        nodes = data['nodes'] + [node for sg in subgraphs for node in sg['nodes']]
        missing = sorted({n['type'] for n in nodes if n['type'] not in info and n['type'] not in virtual})
        if missing:
            raise RuntimeError(workflow.name + ': faltan nodos ' + ', '.join(missing))
        event(workflow.name + ': tipos de nodo presentes')
    for m in MANIFEST['models']:
        if not comprobado(BASE / 'models' / m['folder'] / m['file'], m):
            raise RuntimeError('Modelo incompleto: ' + m['file'])
    event('Todos los modelos tienen tamaño y/o SHA256 correcto')
    STATUS['bytes'] = STATUS['total']
    STATUS['estado'] = 'listo'
    event('LISTO: 0 FALLOS')

def esperado():
    return {'comfyui': '0.44.2', 'nodos': {n['folder']: n['commit'] for n in MANIFEST['nodes']}}

def imagen_al_dia():
    try:
        marca = json.loads(MARCA.read_text())
    except (OSError, ValueError):
        return False, 'no es la imagen PMS'
    if {k: marca.get(k) for k in ('comfyui', 'nodos')} != esperado():
        return False, 'la imagen trae otra versión de ComfyUI o de los nodos que modelos.json'
    return True, 'imagen PMS ' + str(marca.get('etiqueta', ''))

def install():
    al_dia, motivo = (False, 'construcción') if CONSTRUIR else imagen_al_dia()
    if not BASE.exists():
        BASE.parent.mkdir(parents=True, exist_ok=True)
        event('Copiando ComfyUI de la imagen al disco (unos minutos)', 'INFO')
        shutil.copytree('/opt/comfyui-baked', BASE)
    if not PY.exists():
        run(['python3.12', '-m', 'venv', '--system-site-packages', PY.parent.parent], 'Python del pod preparado')
    if not CONSTRUIR:
        run(['nvidia-smi'], 'GPU disponible')
    for cmd in ('ffmpeg', 'ffprobe', 'git', 'curl'):
        if not shutil.which(cmd):
            raise RuntimeError('Falta ' + cmd + ' en la imagen del pod')
    if MARCA.exists() and not CONSTRUIR:
        # Los modelos van al disco grande; la imagen solo trae la estructura vacía de carpetas.
        if not MODELOS_IMAGEN.exists():
            shutil.copytree(IMAGEN / 'models_base', MODELOS_IMAGEN)
    if not CONSTRUIR:
        remaining = sum(m['bytes'] or 160000000 for m in MANIFEST['models'] if not valid(BASE/'models'/m['folder']/m['file'], m))
        if shutil.disk_usage((BASE / 'models').resolve()).free < remaining + 15_000_000_000:
            raise RuntimeError('Disco insuficiente para modelos y temporales')
        event('Disco suficiente; modelos: ' + str(round(STATUS['total']/1e9, 1)) + ' GB en disco. La VRAM se consulta en nvidia-smi.')
    if al_dia:
        event('Imagen PMS al día (' + motivo + '): ComfyUI 0.44.2, 7 nodos y dependencias ya instalados; se salta pip')
        install_deps = False
    else:
        if not CONSTRUIR:
            event('Instalación completa de nodos y dependencias: ' + motivo, 'INFO')
        install_deps = True
    if install_deps:
        instalar_dependencias()
    if CONSTRUIR:
        MARCA.write_text(json.dumps(dict(esperado(), etiqueta=os.environ.get('PMS_ETIQUETA', ''), creada=time.strftime('%Y-%m-%d %H:%M')), indent=2))
        event('Imagen preparada: ' + str(MARCA))
        return
    descargar_y_publicar()

def instalar_dependencias():
    constraints = TMP / 'constraints.txt'
    versions = subprocess.check_output([str(PY), '-c', 'import importlib.metadata as m; print("\\n".join(n+"=="+m.version(n) for n in ["torch","torchvision","torchaudio"]))'], text=True)
    constraints.write_text(versions + '\ntransformers>=4.57,<5\ndiffusers==0.35.2\naccelerate==1.10.1\npeft==0.17.1\n', encoding='utf-8')
    env = dict(os.environ, PIP_CONSTRAINT=str(constraints), PIP_DISABLE_PIP_VERSION_CHECK='1')
    version = BASE / 'comfyui_version.py'
    if not version.exists() or '0.44.2' not in version.read_text():
        if not (BASE / '.git').exists():
            raise RuntimeError('La imagen no contiene el repositorio git de ComfyUI')
        run(['git', 'fetch', '--depth', '1', 'origin', 'tag', 'v0.44.2'], 'ComfyUI: etiqueta descargada', BASE)
        # El checkout es sobre la copia nueva de la imagen, nunca sobre instalaciones de la PC.
        run(['git', '-c', 'advice.detachedHead=false', 'checkout', 'v0.44.2'], 'ComfyUI actualizado', BASE)
    run([PY, '-m', 'pip', 'install', '-r', BASE / 'requirements.txt'], 'Dependencias de ComfyUI', env=env)
    for node in MANIFEST['nodes']:
        dest = BASE / 'custom_nodes' / node['folder']
        if not (dest / '.git').exists():
            run(['git', 'init', dest], 'Preparar nodo ' + node['folder'])
            run(['git', '-C', dest, 'remote', 'add', 'origin', node['repo']], 'Origen ' + node['folder'])
        run(['git', '-C', dest, 'fetch', '--depth', '1', 'origin', node['commit']], 'Descargar nodo ' + node['folder'])
        run(['git', '-C', dest, '-c', 'advice.detachedHead=false', 'checkout', 'FETCH_HEAD'], 'Fijar nodo ' + node['folder'])
        req = dest / 'requirements.txt'
        if node['requirements'] and req.exists():
            run([PY, '-m', 'pip', 'install', '-r', req], 'Dependencias ' + node['folder'], env=env)
    run([PY, '-m', 'pip', 'install', 'transformers>=4.57,<5'], 'Transformers compatible con Qwen3-VL', env=env)
    after = subprocess.check_output([str(PY), '-c', 'import importlib.metadata as m; print("\\n".join(n+"=="+m.version(n) for n in ["torch","torchvision","torchaudio"]))'], text=True)
    if versions != after:
        raise RuntimeError('Cambió PyTorch durante la instalación')
    event('PyTorch de la imagen conservado')

def descargar_y_publicar():
    STATUS['estado'] = 'bajando'
    # Una descarga a la vez; el monitor permanece activo durante toda la descarga.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        for m in MANIFEST['models']:
            task = pool.submit(download, m)
            last = time.time(); before = STATUS['bytes']
            while not task.done():
                progress()
                if time.time() - last >= 20:
                    speed = max(0, STATUS['bytes']-before)/1e6/(time.time()-last)
                    event(time.strftime('%H:%M:%S')+' '+str(round(STATUS['bytes']/1e9,2))+' de '+str(round(STATUS['total']/1e9,2))+' GB; '+str(round(speed,1))+' MB/s', 'INFO')
                    last=time.time(); before=STATUS['bytes']
                time.sleep(1)
            task.result()  # Primer fallo detiene todo; no se inicia el siguiente archivo.
    wf_dir = BASE / 'user/default/workflows'
    wf_dir.mkdir(parents=True, exist_ok=True)
    for f in (PACK / 'workflows').glob('*.json'):
        backup(wf_dir / f.name)
        shutil.copy2(f, wf_dir / f.name)
    custom = BASE / 'custom_nodes/pms_rodaje'
    custom.mkdir(parents=True, exist_ok=True)
    (custom / '__init__.py').write_text('NODE_CLASS_MAPPINGS = {}\nNODE_DISPLAY_NAME_MAPPINGS = {}\n')
    link = custom / 'example_workflows'
    if not link.exists():
        link.symlink_to(wf_dir, target_is_directory=True)
    event('Tres workflows instalados')

def main():
    try:
        publish()
        if '--revisar' in sys.argv:
            inspect()
        else:
            install()
        return 0
    except Exception as exc:
        event(str(exc), 'FALLO')
        return 1

if __name__ == '__main__':
    sys.exit(main())
