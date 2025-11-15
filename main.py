import argparse
import configparser
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Optional, Callable, Iterable, Tuple
from urllib import request, error


class ConfigError(Exception):
    """Базовая ошибка конфигурации."""


class DependencyFetchError(Exception):
    """Ошибка при получении/построении зависимостей пакета."""


@dataclass
class AppConfig:
    package_name: str
    version: str
    mode: str  # "real" или "test"
    repo_url: Optional[str]
    test_repo_path: Optional[str]
    ascii_tree: bool


def parse_bool(value: str) -> bool:
    true_values = {"1", "true", "yes", "on"}
    false_values = {"0", "false", "no", "off"}

    v = value.strip().lower()
    if v in true_values:
        return True
    if v in false_values:
        return False
    raise ConfigError(
        f"Некорректное булево значение: {value!r}. "
        f"Ожидалось одно из: {', '.join(sorted(true_values | false_values))}"
    )


def validate_version(version: str) -> None:
    parts = version.split(".")
    if not 2 <= len(parts) <= 3:
        raise ConfigError(
            f"Некорректная версия пакета: {version!r}. "
            "Ожидался формат X.Y или X.Y.Z"
        )
    for p in parts:
        if not p.isdigit():
            raise ConfigError(
                f"Некорректная версия пакета: {version!r}. "
                "Части версии должны быть числами."
            )


def load_config(path: str) -> AppConfig:
    if not os.path.exists(path):
        raise ConfigError(f"Файл конфигурации не найден: {path}")

    parser = configparser.ConfigParser()
    try:
        read_files = parser.read(path, encoding="utf-8")
    except configparser.Error as e:
        raise ConfigError(f"Ошибка чтения INI файла: {e}") from e

    if not read_files:
        raise ConfigError(f"Не удалось прочитать файл конфигурации: {path}")

    if "app" not in parser:
        raise ConfigError("Секция [app] отсутствует в файле конфигурации.")

    section = parser["app"]

    package_name = section.get("package_name", "").strip()
    if not package_name:
        raise ConfigError("Параметр 'package_name' обязателен и не может быть пустым.")

    version = section.get("version", "").strip()
    if not version:
        raise ConfigError("Параметр 'version' обязателен и не может быть пустым.")
    validate_version(version)

    mode = section.get("mode", "").strip().lower()
    if mode not in {"real", "test"}:
        raise ConfigError(
            f"Некорректный режим работы 'mode': {mode!r}. "
            "Допустимые значения: 'real' или 'test'."
        )

    repo_url: Optional[str] = None
    test_repo_path: Optional[str] = None

    if mode == "real":
        repo_url = section.get("repo_url", "").strip()
        if not repo_url:
            raise ConfigError("Режим 'real': параметр 'repo_url' обязателен.")
        if not (repo_url.startswith("http://") or repo_url.startswith("https://")):
            raise ConfigError(
                f"Некорректный 'repo_url': {repo_url!r}. "
                "Ожидался URL, начинающийся с 'http://' или 'https://'."
            )

    elif mode == "test":
        test_repo_path = section.get("test_repo_path", "").strip()
        if not test_repo_path:
            raise ConfigError("Режим 'test': параметр 'test_repo_path' обязателен.")
        if not os.path.exists(test_repo_path):
            raise ConfigError(
                f"Режим 'test': файл тестового репозитория не найден: {test_repo_path}"
            )

    ascii_tree_raw = section.get("ascii_tree", "false")
    ascii_tree = parse_bool(ascii_tree_raw)

    return AppConfig(
        package_name=package_name,
        version=version,
        mode=mode,
        repo_url=repo_url,
        test_repo_path=test_repo_path,
        ascii_tree=ascii_tree,
    )


def print_config(config: AppConfig) -> None:
    data = {
        "package_name": config.package_name,
        "version": config.version,
        "mode": config.mode,
        "repo_url": config.repo_url or "",
        "test_repo_path": config.test_repo_path or "",
        "ascii_tree": str(config.ascii_tree).lower(),
    }
    for key, value in data.items():
        print(f"{key} = {value}")


def build_metadata_url_for_root(config: AppConfig) -> str:
    if config.mode != "real":
        raise DependencyFetchError(
            "Получение данных возможно только в режиме 'real'."
        )
    base = (config.repo_url or "").rstrip("/")
    return f"{base}/{config.package_name}/{config.version}/json"


def build_metadata_url_latest(config: AppConfig, package_name: str) -> str:
    if config.mode != "real":
        raise DependencyFetchError(
            "Получение данных возможно только в режиме 'real'."
        )
    base = (config.repo_url or "").rstrip("/")
    return f"{base}/{package_name}/json"


def fetch_metadata_json(url: str) -> dict:
    try:
        with request.urlopen(url) as resp:
            if resp.status != 200:
                raise DependencyFetchError(
                    f"Сервер вернул статус {resp.status} при запросе {url!r}"
                )
            data = resp.read()
    except error.HTTPError as e:
        raise DependencyFetchError(
            f"HTTP ошибка при запросе {url!r}: {e.code} {e.reason}"
        ) from e
    except error.URLError as e:
        raise DependencyFetchError(
            f"Ошибка сети при запросе {url!r}: {e.reason}"
        ) from e

    try:
        return json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise DependencyFetchError(
            f"Не удалось разобрать JSON-ответ от {url!r}: {e}"
        ) from e


def parse_direct_dependencies_raw(metadata: dict) -> list[str]:
    info = metadata.get("info")
    if not isinstance(info, dict):
        raise DependencyFetchError("Неверный формат метаданных: нет 'info'.")

    requires = info.get("requires_dist")
    if requires is None:
        return []

    if not isinstance(requires, list):
        raise DependencyFetchError("'requires_dist' должен быть списком.")

    result = []
    for item in requires:
        if not isinstance(item, str):
            continue
        base = item.split(";", 1)[0].strip()
        if base:
            result.append(base)
    return result


def extract_package_name_from_requirement(req: str) -> Optional[str]:
    s = req.split(";", 1)[0].strip()
    if not s:
        return None
    m = re.match(r"([A-Za-z0-9_.\-]+)", s)
    return m.group(1) if m else None


def parse_direct_dependency_names(metadata: dict) -> list[str]:
    raw = parse_direct_dependencies_raw(metadata)
    result = []
    for r in raw:
        name = extract_package_name_from_requirement(r)
        if name:
            result.append(name)
    return result


def print_direct_dependencies(config: AppConfig) -> None:
    url = build_metadata_url_for_root(config)
    metadata = fetch_metadata_json(url)
    deps = parse_direct_dependencies_raw(metadata)

    print()
    print(f"Прямые зависимости пакета {config.package_name}=={config.version}:")
    if not deps:
        print("  (нет прямых зависимостей)")
    else:
        for dep in deps:
            print(f"  - {dep}")


def bfs_recursive(
        start_nodes: Iterable[str],
        get_neighbors: Callable[[str], Iterable[str]]
) -> Tuple[dict[str, set[str]], set[Tuple[str, str]]]:
    graph: dict[str, set[str]] = {}
    visited: set[str] = set()
    cycles: set[Tuple[str, str]] = set()

    def bfs_level(frontier: list[str]) -> None:
        if not frontier:
            return

        next_frontier = []

        for node in frontier:
            if node in visited:
                continue
            visited.add(node)

            neighbors = list(get_neighbors(node))
            node_neighbors = graph.setdefault(node, set())

            for nb in neighbors:
                node_neighbors.add(nb)
                if nb in visited:
                    cycles.add((node, nb))
                else:
                    next_frontier.append(nb)

        if next_frontier:
            bfs_level(next_frontier)

    bfs_level(list(start_nodes))
    return graph, cycles


def load_test_repo_graph(path: str) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}

    try:
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if ":" not in stripped:
                    raise DependencyFetchError(
                        f"Ошибка в строке {lineno}: ожидалось 'A: B C'."
                    )
                name_part, deps_part = stripped.split(":", 1)
                name = name_part.strip()
                if not name.isalpha() or not name.isupper():
                    raise DependencyFetchError(
                        f"Некорректное имя пакета '{name}' в строке {lineno}."
                    )
                deps_raw = deps_part.replace(",", " ").split()
                deps = []
                for d in deps_raw:
                    if not d.isalpha() or not d.isupper():
                        raise DependencyFetchError(
                            f"Некорректная зависимость '{d}' в строке {lineno}."
                        )
                    deps.append(d)
                graph[name] = deps

        all_deps = {d for lst in graph.values() for d in lst}
        for d in all_deps:
            graph.setdefault(d, [])
    except OSError as e:
        raise DependencyFetchError(f"Ошибка чтения {path!r}: {e}") from e

    return graph


def build_dependency_graph_real(config: AppConfig) -> Tuple[dict[str, set[str]], set[Tuple[str, str]]]:
    cache: dict[str, list[str]] = {}

    def get_neighbors(pkg: str) -> list[str]:
        if pkg in cache:
            return cache[pkg]

        if pkg == config.package_name:
            url = build_metadata_url_for_root(config)
        else:
            url = build_metadata_url_latest(config, pkg)

        metadata = fetch_metadata_json(url)
        neighbors = parse_direct_dependency_names(metadata)
        cache[pkg] = neighbors
        return neighbors

    return bfs_recursive([config.package_name], get_neighbors)


def build_dependency_graph_test(config: AppConfig) -> Tuple[dict[str, set[str]], set[Tuple[str, str]]]:
    if not config.test_repo_path:
        raise DependencyFetchError("Не указан test_repo_path.")

    repo_graph = load_test_repo_graph(config.test_repo_path)

    if config.package_name not in repo_graph:
        raise DependencyFetchError(
            f"Пакет {config.package_name!r} отсутствует в тестовом репозитории."
        )

    def get_neighbors(pkg: str) -> list[str]:
        return repo_graph.get(pkg, [])

    return bfs_recursive([config.package_name], get_neighbors)


def print_dependency_graph(
        graph: dict[str, set[str]],
        cycles: set[Tuple[str, str]],
        root: str
) -> None:
    print()
    print("Граф зависимостей:")
    print(f"Корневой пакет: {root}\n")

    for node in sorted(graph.keys()):
        deps = sorted(graph[node]) if graph[node] else []
        if deps:
            print(f"  {node} -> {', '.join(deps)}")
        else:
            print(f"  {node} -> (нет зависимостей)")

    print()
    if cycles:
        print("Обнаружены циклы:")
        for u, v in sorted(cycles):
            print(f"  {u} -> {v}")
    else:
        print("Циклические зависимости не обнаружены.")


def build_reverse_graph(graph: dict[str, set[str]]) -> dict[str, set[str]]:
    reverse: dict[str, set[str]] = {}

    for u, neighbors in graph.items():
        reverse.setdefault(u, set())
        for v in neighbors:
            reverse.setdefault(v, set()).add(u)

    return reverse


def print_reverse_dependencies(graph: dict[str, set[str]], root: str) -> None:
    reverse_graph = build_reverse_graph(graph)

    subgraph, _ = bfs_recursive(
        [root],
        lambda node: reverse_graph.get(node, set())
    )

    dependents = sorted(n for n in subgraph.keys() if n != root)

    print()
    print(f"Обратные зависимости для пакета {root}:")
    if not dependents:
        print("  (нет пакетов, которые зависят от этого пакета)")
    else:
        for pkg in dependents:
            print(f"  - {pkg}")


# ---------- Graphviz (DOT) ----------

def build_graphviz_dot(graph: dict[str, set[str]], root: str) -> str:
    lines: list[str] = []
    lines.append("digraph dependencies {")
    lines.append(f'  label="Dependencies for {root}";')
    lines.append("  labelloc=top;")
    lines.append("  node [shape=ellipse];")

    # Явно выводим узлы без рёбер, чтобы они появились на диаграмме
    all_nodes = set(graph.keys()) | {n for deps in graph.values() for n in deps}
    for node in sorted(all_nodes):
        lines.append(f'  "{node}";')

    for u in sorted(graph.keys()):
        for v in sorted(graph[u]):
            lines.append(f'  "{u}" -> "{v}";')

    lines.append("}")
    return "\n".join(lines)


def print_graphviz_dot(graph: dict[str, set[str]], root: str) -> None:
    print()
    print("Представление графа в формате Graphviz (DOT):")
    dot = build_graphviz_dot(graph, root)
    print(dot)


# ---------- ASCII-дерево ----------

def print_ascii_tree(graph: dict[str, set[str]], root: str) -> None:
    print()
    print("ASCII-дерево зависимостей:")
    print(root)

    def _print_children(node: str, prefix: str, path: set[str]) -> None:
        children = sorted(graph.get(node, set()))
        for idx, child in enumerate(children):
            is_last = (idx == len(children) - 1)
            connector = "└── " if is_last else "├── "
            line_prefix = prefix + connector

            if child in path:
                print(f"{line_prefix}{child} (cycle)")
                continue

            print(f"{line_prefix}{child}")
            new_prefix = prefix + ("    " if is_last else "│   ")
            _print_children(child, new_prefix, path | {child})

    _print_children(root, "", {root})


# ---------- main ----------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Инструмент визуализации графа зависимостей.\n"
            "Поддерживает этапы 1–5."
        )
    )
    parser.add_argument(
        "-c", "--config",
        default="config.ini",
        help="Путь к INI файлу конфигурации"
    )
    parser.add_argument(
        "--no-config-print",
        action="store_true",
        help="Не выводить параметры конфигурации"
    )
    parser.add_argument(
        "--reverse-deps",
        action="store_true",
        help="Вывести обратные зависимости для пакета"
    )

    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"Ошибка конфигурации: {e}", file=sys.stderr)
        sys.exit(1)

    if not args.no_config_print:
        print_config(config)

    if config.mode == "real":
        try:
            print_direct_dependencies(config)
        except Exception as e:
            print(f"Ошибка получения прямых зависимостей: {e}", file=sys.stderr)

    try:
        if config.mode == "real":
            graph, cycles = build_dependency_graph_real(config)
        else:
            graph, cycles = build_dependency_graph_test(config)

        print_dependency_graph(graph, cycles, config.package_name)
        print_graphviz_dot(graph, config.package_name)

        if config.ascii_tree:
            print_ascii_tree(graph, config.package_name)

        if args.reverse_deps:
            print_reverse_dependencies(graph, config.package_name)

    except Exception as e:
        print(f"Ошибка построения графа зависимостей: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
