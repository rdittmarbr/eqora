#!/usr/bin/env python3

import argparse
import os
import shutil
import sys
from pathlib import Path
import re
import glob
from typing import Optional, Set

try:
    import yaml  # PyYAML
except ImportError:
    print("PyYAML não encontrado. Instale com: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

# -----------------------------------------------------------------------------
# Funcoes auxiliares

def expand(path_str: str) -> str:
    return os.path.expandvars(os.path.expanduser(path_str))

def resolve_context(compose_dir: Path, context_value) -> Path:
    if not context_value:
        context_value = "."
    ctx = Path(expand(str(context_value)))
    if not ctx.is_absolute():
        ctx = (compose_dir / ctx).resolve()
    return ctx

def load_compose(compose_path: Path) -> dict:
    with compose_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "services" not in data or not isinstance(data["services"], dict):
        raise ValueError("Arquivo docker-compose.yaml não contém 'services'.")
    return data

def load_env_file(file_path: Path, override: bool = True) -> None:
    """
    Carrega variáveis no formato KEY=VAL.
    Ignora linhas em branco e comentários. Suporta 'export KEY=VAL'.
    Por padrão sobrescreve variáveis atuais (como o docker-compose faz).
    """
    if not file_path.exists():
        raise FileNotFoundError(f"env_file não encontrado: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].strip()

            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()

            # remove aspas simples/duplas circundantes
            if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
                val = val[1:-1]

            if override or key not in os.environ:
                os.environ[key] = val

def _scan_copy_sources(dockerfile_path: Path):
    """
    Lê o Dockerfile e retorna uma lista de tuplas (src_rel, linha)
    apenas para COPY que tenham UMA origem no formato:
      .docker/<arquivo>
    - Não aceita subpastas: .docker/arquivo (sem '/')
    - Ignora outros formatos/complexidades
    """
    sources = []
    try:
        text = dockerfile_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return sources

    plain_re = re.compile(r'^\s*COPY\s+([^\s]+)\s+[^\s]+', re.IGNORECASE)
    json_re  = re.compile(r'^\s*COPY\s*\[\s*"([^"]+)"\s*,\s*"[^"]+"\s*\]', re.IGNORECASE)

    for i, line in enumerate(text.splitlines(), start=1):
        m1 = plain_re.match(line)
        m2 = json_re.match(line)
        src = None
        if m1:
            src = m1.group(1).strip()
        elif m2:
            src = m2.group(1).strip()

        if not src:
            continue

        # Apenas .docker/<arquivo> sem subpastas
        if src.startswith(".docker/"):
            tail = src[len(".docker/"):]
            if tail and ("/" not in tail):
                sources.append((src, i))
            else:
                sources.append((f"[INVALIDO:{src}]", i))
        else:
            sources.append((f"[FORA_DOT_DOCKER:{src}]", i))

    return sources

def _find_nginx_conf_from_service(svc_def: dict, compose_path: Path) -> Optional[Path]:
    """
    Procura em volumes (string ou dict) um bind cujo target seja /etc/nginx/nginx.conf.
    Retorna o caminho de origem (host) resolvido ou None.
    """
    vols = svc_def.get("volumes") or []
    for v in vols:

        src = tgt = None
        if isinstance(v, str):
            parts = v.split(":", 2)  # host:container[:mode]
            if len(parts) >= 2:
                src, tgt = parts[0], parts[1]
        elif isinstance(v, dict):
            tgt = v.get("target") or v.get("destination") or v.get("dst")
            src = v.get("source")
            bind = v.get("bind")
            if not src and isinstance(bind, dict):
                src = bind.get("source")
        if tgt and str(tgt).rstrip().endswith("/etc/nginx/nginx.conf") and src:
            p = Path(expand(str(src)))
            if not p.is_absolute():
                p = (compose_path.parent / p).resolve()
            return p
    return None

def _extract_server_names_from_file(conf_file: Path, visited: Set[Path], depth: int = 0, max_depth: int = 1) -> Set[str]:
    """
    Extrai nomes de servidor de um arquivo nginx.conf, seguindo 'include ...;' com glob.
    Limita profundidade a max_depth para evitar recursão infinita.
    """
    names: Set[str] = set()
    if conf_file in visited or not conf_file.exists():
        return names
    visited.add(conf_file)

    try:
        text = conf_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return names

    for line in text.splitlines():
        m = re.match(r'^\s*server_name\s+(.+?);', line)
        if not m:
            continue
        tokens = re.split(r'\s+', m.group(1).strip())
        for t in tokens:
            t = t.strip().strip(";")
            if not t or t in ("_", "localhost") or "$" in t:
                continue
            names.add(t.rstrip("."))

    if depth < max_depth:
        base = conf_file.parent
        for inc in re.findall(r'^\s*include\s+([^;]+);', text, flags=re.MULTILINE):
            inc = inc.strip().strip('"\'')
            inc_path = Path(expand(inc))
            if not inc_path.is_absolute():
                inc_path = (base / inc_path)
            for f in glob.glob(str(inc_path)):
                names |= _extract_server_names_from_file(Path(f), visited, depth + 1, max_depth)
    return names

def _check_hosts(domains: Set[str], env_type: str, project_data: Path, color):
    RED, YELLOW, GREEN, RESET = color
    try:
        hosts_text = Path("/etc/hosts").read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"    [NGINX] {RED}Erro:{RESET} não foi possível ler /etc/hosts: {e}")
        return

    project_data.mkdir(parents=True, exist_ok=True)
    snap = project_data / f"hosts-{env_type}.txt"
    try:
        snap.write_text(hosts_text, encoding="utf-8")
        print(f"    [NGINX] Hosts snapshot: {GREEN}{snap}{RESET}")
    except Exception as e:
        print(f"    [NGINX] {YELLOW}Aviso:{RESET} falha ao gravar snapshot {snap}: {e}")

    present: Set[str] = set()
    for raw in hosts_text.splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        raw = raw.split("#", 1)[0].strip()
        cols = re.split(r'\s+', raw)
        for h in cols[1:]:
            if h:
                present.add(h)

    missing = sorted([d for d in domains if d not in present])
    if not missing:
        print(f"    [NGINX] {GREEN}OK:{RESET} todos os domínios do nginx.conf estão no /etc/hosts.")
    else:
        print(f"    [NGINX] {YELLOW}Faltando no /etc/hosts:{RESET}")
        for d in missing:
            print(f"      - {d}")
        print("\n    [NGINX] Sugestão (DEV):")
        print(f"      echo \"127.0.0.1 {' '.join(missing)}\" | sudo tee -a /etc/hosts")

# -----------------------------------------------------------------------------

# main
def main():

    # -------------------------------------------------------------------------
    # Cores
    RED    = "\033[1;31m"
    YELLOW = "\033[1;33m"
    GREEN  = "\033[1;32m"
    RESET  = "\033[0m"

    parser = argparse.ArgumentParser(
        description="Valida serviços do docker-compose e copia um Dockerfile-fonte central "
                    "para <context>/.docker/<nome-do-dockerfile-do-compose>.")
    parser.add_argument("-f", "--file", default="docker-compose.yaml",
                        help="Caminho do docker-compose.yaml (default: docker-compose.yaml)")
    parser.add_argument("--copy", action="store_true",
                        help="Copiar Dockerfile-fonte para <context>/.docker/ (preserva o nome definido no compose)")
    parser.add_argument("--env-type", dest="env_type", default=None,
                        help="Define ENV_TYPE (sobrepõe variável de ambiente)")
    parser.add_argument("--env-file", dest="env_files", action="append", default=[],
                        help="Arquivo(s) .env para carregar (pode repetir a opção). Ex.: --env-file .env --env-file .env.local")
    args = parser.parse_args()

    # Carregando env_files primeiro
    compose_path = Path(args.file).resolve()

    try:
        for ef in args.env_files:
            ef_path = Path(expand(ef))
            load_env_file(ef_path, override=True)
    except FileNotFoundError as e:
        print(f"{e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Falha carregando env_file: {e}", file=sys.stderr)
        return 2

    # Define ENV_TYPE (CLI > ENV > default)
    if args.env_type is not None and args.env_type != "":
        chosen = args.env_type
        source = "--env-type"
    else:
        env_current = os.environ.get("ENV_TYPE", "")
        if env_current:
            chosen = env_current
            source = "ENV"
        else:
            chosen = "development"
            source = "default"
    os.environ["ENV_TYPE"] = chosen

    try:

        print(f"Iniciando a validação do compose")

        if not compose_path.exists():
            raise FileNotFoundError(f"  Não foi possível carregar: {compose_path}")

        cfg = load_compose(compose_path)
        services = cfg.get("services", {})

        print(f"  Ambiente: {GREEN}{os.environ['ENV_TYPE']}{RESET} ({YELLOW}{source}{RESET})")
        print(f"  Arquivo Compose: {GREEN}{compose_path}{RESET}")

        if args.env_files:
            print(f"  Variáveis de ambiente: {GREEN}{', '.join(args.env_files)}{RESET}")

        # Validando os servicos
        for svc_name, svc_def in services.items():
            print(f"")
            print(f"  Serviço: {GREEN}{svc_name}{RESET}")

            build = svc_def.get("build")
            if build is None:
                print(f"    {YELLOW}build ausente{RESET}\n")
            else:

                # build pode ser string (context) ou dict
                if isinstance(build, str):
                    context_val = build
                    dockerfile_val = None
                    dockerfile_path = None
                elif isinstance(build, dict):
                    context_val = build.get("context", ".")
                    dockerfile_val = expand(str(build.get("dockerfile", "Dockerfile")))
                    dockerfile_path = Path(dockerfile_val).resolve()
                else:
                    raise ValueError(f"Serviço {svc_name}: formato de build desconhecido")

                if not dockerfile_val:
                    print(f"    {YELLOW}Parâmetro dockerfile ausente{RESET}\n")
                    continue

                print(f"    context : {GREEN}{context_val}{RESET}")

                if not dockerfile_path.exists():
                    raise FileNotFoundError(f"    Dockerfile: {YELLOW}{dockerfile_path}{RESET} - {RED}Erro ao validar{RESET}")
                print(f"    Dockerfile: {GREEN}{dockerfile_val}{RESET}")

                # Resolve o diretório de contexto relativo ao compose
                context_dir = resolve_context(compose_path.parent, context_val)
                dest_dir = context_dir / ".docker"
                dest_name = Path(dockerfile_val).name  # preserva o nome definido no compose
                dest_path = dest_dir / dest_name

                # Só executa se origem e destino forem diferentes
                if dockerfile_path != dest_path:

                    # Se não foi solicitado copiar, apenas valida e informa
                    if not args.copy:
                        print(f"    {YELLOW}[no --copy]{RESET}: Arquivo não copiado. {RED}{dest_path}{RESET}.")
                        # Varrendo Dockerfile de origem para listar os COPY
                        scan_target = dockerfile_path
                    else:
                        # Garante a pasta .docker
                        try:
                            dest_dir.mkdir(parents=True, exist_ok=True)
                        except Exception as e:
                            raise IOError(f"    {RED}Falha ao criar diretório de destino {dest_dir}{RESET}: {e}")

                        # Copia preservando metadata básica
                        try:
                            shutil.copy2(dockerfile_path, dest_path)
                            print(f"    Arquivo copiado com sucesso para {GREEN}{dest_path}{RESET}.")
                        except Exception as e:
                            raise IOError(f"    Falha ao copiar para {dest_path}: {e}")
                        scan_target = dest_path  # varreremos o arquivo copiado

                    # -------------------------------------------------------------
                    # Verifica os comandos copy do Dockerfile
                    # atenção : aqui deve ser verificado se no dockerfile existe o copy para copiar os arquivos.
                    # neste copy deve ser levado em consideração somente o que está na pasta .docker
                    # não será copiado subpastas
                    # será copiado do ./docker/arquivo para o contexto./docker/arquivo
                    copies = _scan_copy_sources(scan_target)
                    print(f"    Varredura de COPY: {len(copies)} origem(ns) encontrada(s)")

                    # Diretórios base
                    project_root = compose_path.parent
                    source_docker_dir = project_root
                    ctx_docker_dir = context_dir / ".docker"

                    for src, lineno in copies:
                        # Casos inválidos ou fora do escopo .docker
                        if src.startswith("[INVALIDO:"):
                            print(f"    [SKIP] Linha {lineno}: {YELLOW}{src}{RESET} - {RED}subpastas não são permitidas{RESET}")
                            continue
                        if src.startswith("[FORA_DOT_DOCKER:"):
                            print(f"    [SKIP] Linha {lineno}: {YELLOW}{src}{RESET} - {RED}origem deve estar em .docker/<arquivo>{RESET}")
                            continue

                        # .docker/arquivo
                        filename = src.split("/", 1)[1]  # parte após ".docker/"
                        src_file = source_docker_dir / filename           # ./docker/arquivo
                        dest_file = ctx_docker_dir / filename             # <context>/.docker/arquivo

                        if not src_file.exists():
                            print(f"    [SKIP] Linha {lineno}: origem não encontrada no fonte: {YELLOW}{src_file}{RESET}")
                            continue

                        try:
                            if args.copy:  # [UPDATE] só copia quando --copy
                                ctx_docker_dir.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(src_file, dest_file)
                                print(f"    [OK] Linha {lineno}: copiado {GREEN}{src_file}{RESET} -> {GREEN}{dest_file}{RESET}")
                            else:
                                print(f"    [OK] Linha {lineno}: {YELLOW}[no --copy]{RESET} previsto para {dest_file}")
                        except Exception as e:
                            print(f"    [SKIP] Linha {lineno}: falha ao copiar {src_file}: {RED}{e}{RESET}")

                    # -------------------------------------------------------------
                    # Copiar também o arquivo .docker/env.<ENV_TYPE> para o contexto
                    env_filename = f"env.{os.environ['ENV_TYPE']}"
                    env_src = source_docker_dir / env_filename
                    env_dst = ctx_docker_dir / env_filename

                    if env_src.exists():
                        if args.copy:
                            try:
                                ctx_docker_dir.mkdir(parents=True, exist_ok=True)
                                shutil.copy2(env_src, env_dst)
                                print(f"    [ENV] Copiado {GREEN}{env_src}{RESET} -> {GREEN}{env_dst}{RESET}")
                            except Exception as e:
                                print(f"    [ENV] {RED}Falha ao copiar{RESET} {env_src}: {e}")
                        else:
                            print(f"    [ENV] {YELLOW}[no --copy]{RESET}: não copiado {env_src} -> {env_dst}")
                    else:
                        print(f"    [ENV] {YELLOW}[SKIP]{RESET}: arquivo não encontrado no fonte: {env_src}")

                else:
                    print(f"    {YELLOW}Arquivo de origem e destino iguais{RESET}\n")

            # ----------- [NGINX-PER-SERVICE] validação no serviço --------------
            if os.environ.get("ENV_TYPE", "development") == "development":

                nginx_conf = _find_nginx_conf_from_service(svc_def, compose_path)
                if nginx_conf:
                    print(f"    [NGINX] nginx.conf: {GREEN}{nginx_conf}{RESET}")  # imprime o nginx.conf validado
                    if not nginx_conf.exists():
                        print(f"    [NGINX] {RED}Erro:{RESET} arquivo não encontrado no host.")
                    else:
                        names = _extract_server_names_from_file(nginx_conf, visited=set(), max_depth=1)
                        if names:
                            print(f"    [NGINX] server_name(s): {', '.join(sorted(names))}")
                        else:
                            print(f"    [NGINX] {YELLOW}Aviso:{RESET} nenhum server_name encontrado.")

                        project_data = Path(os.environ.get("PROJECT_DATA", str(compose_path.parent / ".docker")))
                        _check_hosts(names, os.environ["ENV_TYPE"], project_data, (RED, YELLOW, GREEN, RESET))
                else:
                    # silencioso se o serviço não for Nginx ou não mapear nginx.conf
                    pass

        print("Processo finalizado com sucesso.")
        return 0

    except FileNotFoundError as e:
        print(f"{e}\n", file=sys.stderr)
        return 2
    except (RuntimeError, ValueError, IOError) as e:
        print(f"{e}\n", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Erro inesperado: {e}", file=sys.stderr)
        return 2

# Executando main
if __name__ == "__main__":
    sys.exit(main())
