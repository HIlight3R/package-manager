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
    # Простая проверка вида X.Y[.Z] — можно усложнить при необходимости
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

    # 1. Имя пакета
    package_name = section.get("package_name", "").strip()
    if not package_name:
        raise ConfigError("Параметр 'package_name' обязателен и не может быть пустым.")

    # 2. Версия
    version = section.get("version", "").strip()
    if not version:
        raise ConfigError("Параметр 'version' обязателен и не может быть пустым.")
    validate_version(version)

    # 3. Режим работы: real / test
    mode = section.get("mode", "").strip().lower()
    if mode not in {"real", "test"}:
        raise ConfigError(
            f"Некорректный режим работы 'mode': {mode!r}. "
            "Допустимые значения: 'real' или 'test'."
        )

    # 4. URL реального репозитория или путь к тестовому
    repo_url: Optional[str] = None
    test_repo_path: Optional[str] = None

    if mode == "real":
        repo_url = section.get("repo_url", "").strip()
        if not repo_url:
            raise ConfigError(
                "Режим 'real': параметр 'repo_url' обязателен и не может быть пустым."
            )
        if not (repo_url.startswith("http://") or repo_url.startswith("https://")):
            raise ConfigError(
                f"Некорректный 'repo_url': {repo_url!r}. "
                "Ожидался URL, начинающийся с 'http://' или 'https://'."
            )

    elif mode == "test":
        test_repo_path = section.get("test_repo_path", "").strip()
        if not test_repo_path:
            raise ConfigError(
                "Режим 'test': параметр 'test_repo_path' обязателен и не может быть пустым."
            )
        if not os.path.exists(test_repo_path):
            raise ConfigError(
                f"Режим 'test': файл тестового репозитория не найден: {test_repo_path}"
            )

    # 5. Режим ASCII-дерева (понадобится на 5 этапе)
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
    """
    Вывод всех параметров в формате ключ = значение.
    """
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
    """
    URL для метаданных *корневого* пакета (имя + версия из конфига).
    Формат PyPI JSON API: /<name>/<version>/json
    """
    if config.mode != "real":
        raise DependencyFetchError(
            "Получение данных о зависимостях из реального репозитория "
            "поддерживается только в режиме 'real'."
        )

    base = (config.repo_url or "").rstrip("/")
    return f"{base}/{config.package_name}/{config.version}/json"


def build_metadata_url_latest(config: AppConfig, package_name: str) -> str:
    """
    URL для метаданных пакета, когда конкретная версия не задана.
    Для PyPI JSON API это /<name>/json (последняя доступная версия).
    Используется для зависимостей.
    """
    if config.mode != "real":
        raise DependencyFetchError(
            "Получение данных о зависимостях из реального репозитория "
            "поддерживается только в режиме 'real'."
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
    except Exception as e:
        raise DependencyFetchError(
            f"Неожиданная ошибка при запросе {url!r}: {e}"
        ) from e

    try:
        return json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise DependencyFetchError(
            f"Не удалось разобрать JSON-ответ от {url!r}: {e}"
        ) from e


def parse_direct_dependencies_raw(metadata: dict) -> list[str]:
    """
    Прямые зависимости в «сыром» виде, как строки из info.requires_dist:
    "urllib3 (<3,>=1.21.1)", "idna (<4,>=2.5)" и т.п.
    Используется для вывода на этапе 2.
    """
    info = metadata.get("info")
    if not isinstance(info, dict):
        raise DependencyFetchError("Неверный формат метаданных: отсутствует объект 'info'.")

    requires = info.get("requires_dist")
    if requires is None:
        return []

    if not isinstance(requires, list):
        raise DependencyFetchError(
            "Неверный формат метаданных: 'requires_dist' должен быть списком."
        )

    dependencies: list[str] = []
    for item in requires:
        if not isinstance(item, str):
            continue
        base_part = item.split(";", 1)[0].strip()
        if not base_part:
            continue
        dependencies.append(base_part)

    return dependencies


def extract_package_name_from_requirement(req: str) -> Optional[str]:
    """
    Из строки вида:
      - "urllib3 (<3,>=1.21.1)"
      - "ruff>=0.6.2"
      - "socks [extra] (<1.0)"
      - "idna (<4,>=2.5) ; python_version >= '3.7'"
    достаём только имя пакета: "urllib3", "ruff", "socks", "idna".
    """
    # Отбрасываем маркеры окружения после ';'
    s = req.split(";", 1)[0].strip()
    if not s:
        return None

    # Берём первые символы, которые выглядят как имя пакета в стиле pip:
    # буквы/цифры/._-
    m = re.match(r"([A-Za-z0-9_.\-]+)", s)
    if not m:
        return None

    name = m.group(1)
    return name or None


def parse_direct_dependency_names(metadata: dict) -> list[str]:
    """
    Прямые зависимости в виде только имён пакетов:
    ["urllib3", "idna", "certifi", ...]
    Используется при построении графа.
    """
    raw = parse_direct_dependencies_raw(metadata)
    result: list[str] = []
    for r in raw:
        name = extract_package_name_from_requirement(r)
        if name:
            result.append(name)
    return result


def print_direct_dependencies(config: AppConfig) -> None:
    """
    Вывод всех прямых зависимостей корневого пакета.
    """
    url = build_metadata_url_for_root(config)
    metadata = fetch_metadata_json(url)
    deps = parse_direct_dependencies_raw(metadata)

    print()
    print(f"Прямые зависимости пакета {config.package_name}=={config.version}:")
    if not deps:
        print("  (нет прямых зависимостей)")
        return

    for dep in deps:
        print(f"  - {dep}")


def bfs_recursive(
        start_nodes: Iterable[str],
        get_neighbors: Callable[[str], Iterable[str]],
) -> Tuple[dict[str, set[str]], set[Tuple[str, str]]]:
    """
    BFS с рекурсией по уровням.
    Возвращает:
      - graph: словарь {узел: множество его прямых зависимостей}
      - cycles: множество рёбер (u, v), которые ведут к уже посещённым вершинам (циклы)
    """
    graph: dict[str, set[str]] = {}
    visited: set[str] = set()
    cycles: set[Tuple[str, str]] = set()

    def bfs_level(frontier: list[str]) -> None:
        if not frontier:
            return

        next_frontier: list[str] = []

        for node in frontier:
            if node in visited:
                continue
            visited.add(node)

            neighbors = list(get_neighbors(node))
            # создаём запись даже если зависимостей нет
            node_neighbors = graph.setdefault(node, set())

            for nb in neighbors:
                node_neighbors.add(nb)
                if nb in visited:
                    # Обнаружили цикл (обратное/боковое ребро)
                    cycles.add((node, nb))
                else:
                    next_frontier.append(nb)

        # Рекурсивный вызов для следующего уровня BFS
        if next_frontier:
            bfs_level(next_frontier)

    bfs_level(list(start_nodes))
    return graph, cycles


def load_test_repo_graph(path: str) -> dict[str, list[str]]:
    """
    Чтение тестового репозитория из файла.
    Формат строк:
        A: B C
        B: C
        C:
    Пакеты — большие латинские буквы.
    """
    graph: dict[str, list[str]] = {}

    try:
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue

                if ":" not in stripped:
                    raise DependencyFetchError(
                        f"Неверный формат файла {path!r}, строка {lineno}: "
                        "ожидалось 'A: B C'."
                    )

                name_part, deps_part = stripped.split(":", 1)
                name = name_part.strip()
                if not name or not name.isalpha() or not name.isupper():
                    raise DependencyFetchError(
                        f"Неверное имя пакета {name!r} в строке {lineno}. "
                        "Ожидались большие латинские буквы."
                    )

                # Разделяем по пробелам/запятым
                deps_raw = deps_part.replace(",", " ").split()
                deps: list[str] = []
                for d in deps_raw:
                    if not d:
                        continue
                    if not d.isalpha() or not d.isupper():
                        raise DependencyFetchError(
                            f"Неверное имя зависимости {d!r} в строке {lineno}. "
                            "Ожидались большие латинские буквы."
                        )
                    deps.append(d)

                graph[name] = deps

        # Добавляем вершины, которые упомянуты только как зависимости
        all_deps = {d for ds in graph.values() for d in ds}
        for d in all_deps:
            graph.setdefault(d, [])
    except OSError as e:
        raise DependencyFetchError(
            f"Не удалось прочитать тестовый репозиторий {path!r}: {e}"
        ) from e

    return graph


def build_dependency_graph_real(config: AppConfig) -> Tuple[dict[str, set[str]], set[Tuple[str, str]]]:
    """
    Построение графа зависимостей для реального pip-репозитория.
    Корневой пакет берётся из config.package_name/config.version,
    для зависимостей берём последнюю доступную версию (через /<name>/json).
    """
    cache: dict[str, list[str]] = {}

    def get_neighbors(pkg: str) -> list[str]:
        if pkg in cache:
            return cache[pkg]

        # Корневой пакет: фиксированная версия из конфига
        if pkg == config.package_name:
            url = build_metadata_url_for_root(config)
        else:
            # Для зависимостей используем "последнюю" версию
            url = build_metadata_url_latest(config, pkg)

        metadata = fetch_metadata_json(url)
        neighbors = parse_direct_dependency_names(metadata)
        cache[pkg] = neighbors
        return neighbors

    graph, cycles = bfs_recursive([config.package_name], get_neighbors)
    return graph, cycles


def build_dependency_graph_test(config: AppConfig) -> Tuple[dict[str, set[str]], set[Tuple[str, str]]]:
    """
    Построение графа зависимостей по тестовому репозиторию.
    Корневой пакет — config.package_name (ожидаем большую букву).
    """
    if not config.test_repo_path:
        raise DependencyFetchError(
            "Режим 'test': не указан путь к файлу тестового репозитория."
        )

    repo_graph = load_test_repo_graph(config.test_repo_path)

    if config.package_name not in repo_graph:
        raise DependencyFetchError(
            f"Режим 'test': пакет {config.package_name!r} отсутствует в тестовом репозитории."
        )

    def get_neighbors(pkg: str) -> list[str]:
        return repo_graph.get(pkg, [])

    graph, cycles = bfs_recursive([config.package_name], get_neighbors)
    return graph, cycles


def print_dependency_graph(
        graph: dict[str, set[str]],
        cycles: set[Tuple[str, str]],
        root: str,
) -> None:
    """
    Вывод всего графа зависимостей и обнаруженных циклов.
    """
    print()
    print(f"Граф зависимостей (ребро A -> B означает 'A зависит от B').")
    print(f"Корневой пакет: {root}")
    print()

    # Сортируем для детерминированного вывода
    for node in sorted(graph.keys()):
        neighbors = graph.get(node, set())
        if neighbors:
            deps = ", ".join(sorted(neighbors))
        else:
            deps = "(нет зависимостей)"
        print(f"  {node} -> {deps}")

    if cycles:
        print()
        print("Обнаружены циклические зависимости (ребро U -> V ведёт в уже посещённую вершину):")
        for u, v in sorted(cycles):
            print(f"  {u} -> {v}")
    else:
        print()
        print("Циклические зависимости не обнаружены.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Инструмент визуализации графа зависимостей.\n"
            "Этап 1: загрузка и валидация конфигурации.\n"
            "Этап 2: получение прямых зависимостей пакета.\n"
            "Этап 3: построение графа зависимостей (BFS с рекурсией, циклы, тестовый режим)."
        )
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.ini",
        help="Путь к INI файлу конфигурации (по умолчанию: config.ini)",
    )
    parser.add_argument(
        "--no-config-print",
        action="store_true",
        help="Не выводить параметры конфигурации.",
    )

    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Ошибка конфигурации: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Необработанная ошибка при чтении конфигурации: {e}", file=sys.stderr)
        sys.exit(1)

    if not args.no_config_print:
        print_config(config)

    if config.mode == "real":
        try:
            print_direct_dependencies(config)
        except DependencyFetchError as e:
            print(f"Ошибка получения прямых зависимостей: {e}", file=sys.stderr)
            # Не выходим сразу — граф всё равно можем попробовать построить
        except Exception as e:
            print(f"Необработанная ошибка при получении прямых зависимостей: {e}", file=sys.stderr)

    try:
        if config.mode == "real":
            graph, cycles = build_dependency_graph_real(config)
        else:  # mode == "test"
            graph, cycles = build_dependency_graph_test(config)

        print_dependency_graph(graph, cycles, config.package_name)
    except DependencyFetchError as e:
        print(f"Ошибка построения графа зависимостей: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Необработанная ошибка при построении графа: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
