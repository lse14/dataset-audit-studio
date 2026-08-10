from __future__ import annotations

import ast
import importlib.util
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend" / "dataset_audit_studio"
TEST_ROOT = PROJECT_ROOT / "tests"
PROJECT_PACKAGE = "dataset_audit_studio"
PRESET_MODULE = "dataset_audit_studio.presets.builtin"
MATERIALIZATION_MODULE = "dataset_audit_studio.app.profile_materialization"
MATERIALIZE_PROFILE_NAME = "materialize_profile"
STYLE_ANALYSIS_MODULE = "dataset_audit_studio.app.style_analysis"
STYLE_PROCESS_MODULE = "dataset_audit_studio.app.style_process"
SELECTION_CHECKPOINT_MODULE = "dataset_audit_studio.jobs.selection_checkpoint"
TASK_SERVICE_MODULE = "dataset_audit_studio.jobs.service"
WORKSPACE_FILE_ACCESS_MODULE = "dataset_audit_studio.workspace.file_access"
WORKSPACE_SERVICE_MODULE = "dataset_audit_studio.workspace.service"
TREE_PUBLISHER_MODULE = "dataset_audit_studio.export.tree_publisher"
EXPORT_SERVICE_MODULE = "dataset_audit_studio.export.service"
SELECTION_REPOSITORY_FACADE_MODULE = "dataset_audit_studio.clustering.selection_repository"
SELECTION_WORKSPACE_MODULE = "dataset_audit_studio.adapters.selection_workspace"
COMPONENT_RUN_REPOSITORY_MODULE = "dataset_audit_studio.adapters.component_run_repository"
R8_BATCH_CHECKPOINT_COMPOSITION_MODULES = (
    "dataset_audit_studio.app.modular_clustering_process",
    "dataset_audit_studio.app.modular_exporting_process",
    "dataset_audit_studio.app.modular_scoring_process",
    "dataset_audit_studio.app.style_process",
    "dataset_audit_studio.jobs.runner",
)
STYLE_LEGACY_MODULES = frozenset(
    {
        "dataset_audit_studio.style.service",
        "dataset_audit_studio.style.process",
    }
)
CANONICAL_PROFILE_NAMES = frozenset(
    {
        "DatasetProfile",
        "ProfileConstraints",
        "PROFILE_CONSTRAINTS",
        "PROFILE_DEFAULT_DISABLED_COMPONENT_IDS",
        "PROFILE_OWNED_COMPONENT_IDS",
        "PROFILE_OWNED_CONFIG_FIELDS",
        "profile_constraints",
        "resolve_dataset_profile",
    }
)
ARCHITECTURE_PACKAGES = (
    "adapters",
    "api",
    "app",
    "benchmarks",
    "clustering",
    "components",
    "core",
    "database",
    "export",
    "export_runs",
    "jobs",
    "latent",
    "main",
    "metrics",
    "model_adapters",
    "presets",
    "reviews",
    "runtime",
    "scanner",
    "scoring",
    "style",
    "workspace",
)
BASELINED_NONTRIVIAL_TOP_LEVEL_SCCS = (
    ("adapters", "clustering", "jobs", "scanner", "scoring", "workspace"),
)
SERVICE_MODULES = (
    ("jobs/service.py", "TaskService"),
    ("workspace/service.py", "WorkspaceService"),
    ("export/service.py", "DatasetExporter"),
    ("export_runs/service.py", "ExportRunService"),
)
SERVICE_RESPONSIBILITY_CLUSTERS = {
    "TaskService": (
        "task lifecycle, lease, and control",
        "phase/checkpoint orchestration and review gates",
        "persistence and view helpers",
        "selection checkpoint compatibility policy delegated",
    ),
    "WorkspaceService": (
        "overview, coverage, and folders",
        "cluster/sample and risk/manual overlay queries",
        "query and policy helpers",
        "thumbnail/directories are facade delegation only",
    ),
    "DatasetExporter": (
        "rewrite preview confirmation and backup execution",
        "rewrite checkpoint/control orchestration",
        "rewrite summary and rollback compensation",
    ),
    "ExportRunService": (
        "copy export-run request validation and first-release completion",
        "output path uniqueness and preview-bound settings",
        "independent export-run pagination and views",
    ),
}


@dataclass(frozen=True, order=True)
class StaticImport:
    source_path: str
    source_module: str
    line: int
    target_module: str
    symbols: tuple[str, ...]


@dataclass(frozen=True, order=True)
class FacadeCaller:
    source_path: str
    line: int
    target_module: str
    symbol: str


@dataclass(frozen=True, order=True)
class FunctionDefinition:
    source_path: str
    source_module: str
    line: int
    name: str


@dataclass(frozen=True)
class ServiceInventory:
    source_path: str
    class_name: str
    file_lines: int
    public_methods: tuple[str, ...]
    private_methods: tuple[str, ...]
    responsibility_clusters: tuple[str, ...]


def _python_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*.py")))


def _module_name(path: Path) -> str:
    if path.is_relative_to(BACKEND_ROOT.parent):
        relative = path.relative_to(BACKEND_ROOT.parent).with_suffix("")
        parts = relative.parts
    else:
        relative = path.relative_to(TEST_ROOT).with_suffix("")
        parts = ("tests", *relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _source_package(path: Path, module_name: str) -> str:
    return module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]


def _resolve_from_module(node: ast.ImportFrom, source_package: str) -> str:
    if node.level == 0:
        return node.module or ""
    if not source_package:
        return ""
    relative_name = "." * node.level + (node.module or "")
    try:
        return importlib.util.resolve_name(relative_name, source_package)
    except ImportError:
        return ""


def _imports_in_file(path: Path) -> tuple[StaticImport, ...]:
    source_path = path.relative_to(PROJECT_ROOT).as_posix()
    source_module = _module_name(path)
    source_package = _source_package(path, source_module)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    records: list[StaticImport] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PROJECT_PACKAGE or alias.name.startswith(
                    f"{PROJECT_PACKAGE}."
                ):
                    records.append(
                        StaticImport(
                            source_path,
                            source_module,
                            node.lineno,
                            alias.name,
                            (),
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            target_module = _resolve_from_module(node, source_package)
            if target_module == PROJECT_PACKAGE or target_module.startswith(
                f"{PROJECT_PACKAGE}."
            ):
                records.append(
                    StaticImport(
                        source_path,
                        source_module,
                        node.lineno,
                        target_module,
                        tuple(sorted(alias.name for alias in node.names)),
                    )
                )
    return tuple(sorted(records))


def static_imports(paths: Iterable[Path]) -> tuple[StaticImport, ...]:
    records = [record for path in sorted(paths) for record in _imports_in_file(path)]
    return tuple(sorted(records))


def backend_imports() -> tuple[StaticImport, ...]:
    return static_imports(_python_files(BACKEND_ROOT))


def backend_and_test_imports() -> tuple[StaticImport, ...]:
    return static_imports((*_python_files(BACKEND_ROOT), *_python_files(TEST_ROOT)))


def _top_level_package(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != PROJECT_PACKAGE:
        return None
    return parts[1]


def top_level_import_edges(imports: Iterable[StaticImport]) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            {
                (source, target)
                for record in imports
                if (source := _top_level_package(record.source_module)) in ARCHITECTURE_PACKAGES
                and (target := _top_level_package(record.target_module)) in ARCHITECTURE_PACKAGES
            }
        )
    )


def package_sccs(imports: Iterable[StaticImport]) -> tuple[tuple[str, ...], ...]:
    graph = {package: set() for package in ARCHITECTURE_PACKAGES}
    for source, target in top_level_import_edges(imports):
        graph[source].add(target)

    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(graph[node]):
            if target not in indices:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[target])
        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while True:
                target = stack.pop()
                on_stack.remove(target)
                component.append(target)
                if target == node:
                    break
            components.append(tuple(sorted(component)))

    for package in sorted(graph):
        if package not in indices:
            visit(package)
    return tuple(sorted(components))


def facade_callers(imports: Iterable[StaticImport]) -> tuple[FacadeCaller, ...]:
    callers = [
        FacadeCaller(
            source_path=record.source_path,
            line=record.line,
            target_module=record.target_module,
            symbol=symbol,
        )
        for record in imports
        if record.target_module == PRESET_MODULE
        for symbol in record.symbols
        if symbol in CANONICAL_PROFILE_NAMES
    ]
    return tuple(sorted(callers))


def function_definitions(
    paths: Iterable[Path],
    name: str,
) -> tuple[FunctionDefinition, ...]:
    definitions: list[FunctionDefinition] = []
    for path in sorted(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                definitions.append(
                    FunctionDefinition(
                        source_path=path.relative_to(PROJECT_ROOT).as_posix(),
                        source_module=_module_name(path),
                        line=node.lineno,
                        name=node.name,
                    )
                )
    return tuple(sorted(definitions))


def class_definitions(
    paths: Iterable[Path],
    name: str,
) -> tuple[FunctionDefinition, ...]:
    definitions: list[FunctionDefinition] = []
    for path in sorted(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == name:
                definitions.append(
                    FunctionDefinition(
                        source_path=path.relative_to(PROJECT_ROOT).as_posix(),
                        source_module=_module_name(path),
                        line=node.lineno,
                        name=node.name,
                    )
                )
    return tuple(sorted(definitions))


def service_inventory() -> tuple[ServiceInventory, ...]:
    inventory: list[ServiceInventory] = []
    for relative_path, class_name in SERVICE_MODULES:
        path = BACKEND_ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        class_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        methods = tuple(
            node.name
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        inventory.append(
            ServiceInventory(
                source_path=path.relative_to(PROJECT_ROOT).as_posix(),
                class_name=class_name,
                file_lines=len(path.read_text(encoding="utf-8").splitlines()),
                public_methods=tuple(name for name in methods if not name.startswith("_")),
                private_methods=tuple(name for name in methods if name.startswith("_")),
                responsibility_clusters=SERVICE_RESPONSIBILITY_CLUSTERS[class_name],
            )
        )
    return tuple(inventory)


def _format_import(record: StaticImport) -> str:
    symbols = ", ".join(record.symbols) if record.symbols else "<module>"
    return (
        f"{record.source_path}:{record.line} -> {record.target_module} "
        f"[{symbols}]"
    )


def render_architecture_report() -> str:
    imports = backend_imports()
    edges = top_level_import_edges(imports)
    lines = ["PRODUCTION_MODULE_INVENTORY"]
    lines.extend(_module_name(path) for path in _python_files(BACKEND_ROOT))
    lines.append("PRODUCTION_TOP_LEVEL_NODES")
    lines.extend(f"{PROJECT_PACKAGE}.{package}" for package in ARCHITECTURE_PACKAGES)
    lines.append("STATIC_IMPORT_EDGES")
    lines.extend(_format_import(record) for record in imports)
    lines.append("BASELINED_TOP_LEVEL_IMPORT_EDGES")
    lines.extend(f"allow {source} -> {target}" for source, target in edges)
    lines.append("CURRENT_TOP_LEVEL_SCCS")
    lines.extend(" + ".join(component) for component in package_sccs(imports))
    lines.append("SCC_BASELINE_POLICY")
    lines.append("no new or expanded top-level SCCs")
    lines.append("BASELINED_NONTRIVIAL_TOP_LEVEL_SCCS")
    lines.extend(" + ".join(component) for component in BASELINED_NONTRIVIAL_TOP_LEVEL_SCCS)
    lines.append("R8_2_TARGETS")
    lines.extend(
        (
            "clustering/selection_repository.py: zero callers before removal",
            "adapters.selection_workspace -> jobs.service",
            "jobs.service -> adapters.component_run_repository",
        )
    )
    lines.append("PRESET_CORE_FACADE_CALLERS")
    lines.extend(
        f"{caller.source_path}:{caller.line} -> "
        f"{caller.target_module}.{caller.symbol}"
        for caller in facade_callers(backend_and_test_imports())
    )
    lines.append("SERVICE_RESPONSIBILITY_INVENTORY")
    for service in service_inventory():
        lines.append(
            f"{service.source_path}:{service.class_name}: lines={service.file_lines}"
        )
        lines.append(f"  public={','.join(service.public_methods)}")
        lines.append(f"  private={','.join(service.private_methods)}")
        lines.extend(f"  cluster={cluster}" for cluster in service.responsibility_clusters)
    return "\n".join(lines)


def test_static_import_graph_is_deterministic_and_sorted() -> None:
    imports = backend_imports()
    assert imports == tuple(sorted(imports))
    assert render_architecture_report() == render_architecture_report()


def test_architecture_inventory_covers_every_production_top_level_package_module() -> None:
    expected_nodes = tuple(
        sorted(
            child.stem if child.is_file() else child.name
            for child in BACKEND_ROOT.iterdir()
            if (
                child.is_file()
                and child.suffix == ".py"
                and child.name != "__init__.py"
            )
            or (child.is_dir() and (child / "__init__.py").is_file())
        )
    )

    assert expected_nodes == ARCHITECTURE_PACKAGES

    report = render_architecture_report()
    assert "PRODUCTION_MODULE_INVENTORY" in report
    assert "dataset_audit_studio.adapters.component_run_repository" in report
    assert "PRODUCTION_TOP_LEVEL_NODES" in report
    assert "BASELINED_TOP_LEVEL_IMPORT_EDGES" in report
    assert "CURRENT_TOP_LEVEL_SCCS" in report
    assert "R8_2_TARGETS" in report
    assert "clustering/selection_repository.py: zero callers before removal" in report
    assert "adapters.selection_workspace -> jobs.service" in report
    assert "jobs.service -> adapters.component_run_repository" in report


def test_current_nontrivial_top_level_sccs_are_baselined() -> None:
    current_sccs = tuple(
        component for component in package_sccs(backend_imports()) if len(component) > 1
    )

    assert current_sccs == BASELINED_NONTRIVIAL_TOP_LEVEL_SCCS


def test_r8_selection_repository_facade_is_absent_without_callers() -> None:
    facade_path = BACKEND_ROOT / "clustering" / "selection_repository.py"
    callers = tuple(
        record
        for record in backend_and_test_imports()
        if record.target_module == SELECTION_REPOSITORY_FACADE_MODULE
    )
    violations = [
        f"{facade_path.relative_to(PROJECT_ROOT).as_posix()}: facade file exists"
        for _ in (None,)
        if facade_path.exists()
    ]
    violations.extend(f"caller {_format_import(record)}" for record in callers)

    assert not violations, (
        "R8.2 selection repository facade must be absent with zero production/test callers:\n"
        + "\n".join(violations)
    )


def test_r10_selection_workspace_and_stage_selector_are_removed() -> None:
    removed_paths = (
        BACKEND_ROOT / "adapters" / "selection_workspace.py",
        BACKEND_ROOT / "app" / "stage_selection.py",
    )
    callers = tuple(
        record
        for record in backend_imports()
        if record.target_module
        in {
            "dataset_audit_studio.adapters.selection_workspace",
            "dataset_audit_studio.app.stage_selection",
        }
    )
    violations = [
        f"removed module still exists: {path.relative_to(PROJECT_ROOT).as_posix()}"
        for path in removed_paths
        if path.exists()
    ]
    violations.extend(f"caller {_format_import(record)}" for record in callers)
    assert not violations, (
        "R10.1 must remove the old selection workspace and stage selector paths:\n"
        + "\n".join(violations)
    )


def test_r8_task_service_uses_injected_component_run_checkpoint_writer() -> None:
    service_path = BACKEND_ROOT / "jobs" / "service.py"
    service_source = service_path.read_text(encoding="utf-8")
    adapter_edges = tuple(
        record
        for record in static_imports((service_path,))
        if record.target_module == COMPONENT_RUN_REPOSITORY_MODULE
    )
    composition_edges = tuple(
        record
        for record in backend_imports()
        if record.source_module in R8_BATCH_CHECKPOINT_COMPOSITION_MODULES
        and record.target_module == COMPONENT_RUN_REPOSITORY_MODULE
        and "ComponentRunRepository" in record.symbols
    )
    violations = [
        f"TaskService adapter edge {_format_import(record)}" for record in adapter_edges
    ]
    if "component_run_repository" in service_source:
        violations.append("TaskService contains a component-run adapter import or dynamic bypass")
    expected_composition = set(R8_BATCH_CHECKPOINT_COMPOSITION_MODULES)
    actual_composition = {record.source_module for record in composition_edges}
    if actual_composition != expected_composition:
        violations.append(
            "batch checkpoint writer composition mismatch: "
            f"expected={sorted(expected_composition)}, actual={sorted(actual_composition)}"
        )

    assert not violations, (
        "R8.2 batch checkpoint writing must be injected outside TaskService:\n"
        + "\n".join(violations)
    )


def test_target_backend_layers_follow_acyclic_dependency_direction() -> None:
    target_packages = frozenset({"core", "components", "presets", "app"})
    allowed_targets = {
        "core": frozenset({"core"}),
        "components": frozenset({"components", "core"}),
        "presets": frozenset({"presets", "core"}),
        "app": frozenset({"app", "components", "presets", "core"}),
    }
    violations = []
    for record in backend_imports():
        source = _top_level_package(record.source_module)
        target = _top_level_package(record.target_module)
        if source not in target_packages or target not in target_packages:
            continue
        if target not in allowed_targets[source]:
            violations.append(_format_import(record))
    assert not violations, (
        "target backend layers must use the approved dependency direction:\n"
        + "\n".join(violations)
    )

    graph = {
        package: set(allowed_targets[package]) - {package}
        for package in sorted(target_packages)
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def assert_acyclic(package: str) -> None:
        assert package not in visiting, (
            "approved target-layer dependency graph must be acyclic: "
            f"{package}"
        )
        if package in visited:
            return
        visiting.add(package)
        for dependency in sorted(graph[package]):
            assert_acyclic(dependency)
        visiting.remove(package)
        visited.add(package)

    for package in sorted(graph):
        assert_acyclic(package)


def test_clustering_selection_compatibility_facade_is_absent() -> None:
    imports = backend_imports()
    all_imports = backend_and_test_imports()
    facade_path = BACKEND_ROOT / "clustering" / "selection_service.py"
    facade_module = "dataset_audit_studio.clustering.selection_service"
    callers = tuple(
        record for record in all_imports if record.target_module == facade_module
    )
    reverse_edges = tuple(
        record
        for record in imports
        if _top_level_package(record.source_module) == "clustering"
        and record.target_module.startswith("dataset_audit_studio.app")
    )
    violations = []
    if facade_path.exists():
        violations.append(
            f"{facade_path.relative_to(PROJECT_ROOT).as_posix()}: facade file exists"
        )
    violations.extend(f"caller {_format_import(record)}" for record in callers)
    violations.extend(f"reverse edge {_format_import(record)}" for record in reverse_edges)
    assert not violations, (
        "clustering selection compatibility facade must be absent:\n"
        + "\n".join(violations)
    )


def test_style_facade_and_process_have_single_app_owners() -> None:
    imports = backend_imports()
    all_imports = backend_and_test_imports()
    legacy_files = (
        BACKEND_ROOT / "style" / "service.py",
        BACKEND_ROOT / "style" / "process.py",
    )
    old_callers = tuple(
        record for record in all_imports if record.target_module in STYLE_LEGACY_MODULES
    )
    reverse_edges = tuple(
        record
        for record in imports
        if _top_level_package(record.source_module) == "style"
        and record.target_module.startswith("dataset_audit_studio.app")
    )
    analyzer_owners = class_definitions(_python_files(BACKEND_ROOT), "StyleAnalyzer")
    summary_owners = class_definitions(_python_files(BACKEND_ROOT), "StyleSummary")
    process_owners = function_definitions(
        _python_files(BACKEND_ROOT), "run_style_subprocess"
    )
    entry_owners = function_definitions(_python_files(BACKEND_ROOT), "_style_process_entry")
    violations = [
        f"{path.relative_to(PROJECT_ROOT).as_posix()}: legacy file exists"
        for path in legacy_files
        if path.exists()
    ]
    violations.extend(f"caller {_format_import(record)}" for record in old_callers)
    violations.extend(f"reverse edge {_format_import(record)}" for record in reverse_edges)
    for name, owners, module in (
        ("StyleAnalyzer", analyzer_owners, STYLE_ANALYSIS_MODULE),
        ("StyleSummary", summary_owners, STYLE_ANALYSIS_MODULE),
        ("run_style_subprocess", process_owners, STYLE_PROCESS_MODULE),
        ("_style_process_entry", entry_owners, STYLE_PROCESS_MODULE),
    ):
        if len(owners) != 1 or owners[0].source_module != module:
            details = ", ".join(
                f"{owner.source_path}:{owner.line} -> {owner.source_module}.{owner.name}"
                for owner in owners
            )
            violations.append(f"{name} owner mismatch: {details}")
    if any({"app", "style"}.issubset(component) for component in package_sccs(imports)):
        violations.append("package SCC still contains app + style")
    assert not violations, (
        "style facade/process ownership must converge to app:\n" + "\n".join(violations)
    )


def test_r10_selection_checkpoint_guard_is_removed_from_jobs() -> None:
    guard_path = BACKEND_ROOT / "jobs" / "selection_checkpoint.py"
    service_path = BACKEND_ROOT / "jobs" / "service.py"
    service_source = service_path.read_text(encoding="utf-8")
    service_tree = ast.parse(service_source, filename=str(service_path))
    task_service = next(
        node
        for node in service_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "TaskService"
    )
    methods = {
        node.name
        for node in task_service.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    guard_callers = tuple(
        record
        for record in backend_and_test_imports()
        if record.target_module == SELECTION_CHECKPOINT_MODULE
    )
    violations = []
    if guard_path.exists():
        violations.append(
            f"removed module still exists: {guard_path.relative_to(PROJECT_ROOT).as_posix()}"
        )
    violations.extend(f"guard caller {_format_import(record)}" for record in guard_callers)
    if "_assert_profile_selection_checkpoint_compatible" in methods:
        violations.append("TaskService still owns the removed selection checkpoint guard")
    if "StageMembership" in service_source or "stage_selection" in service_source:
        violations.append("TaskService still contains removed stage-selection policy")
    assert not violations, (
        "R10.1 must remove the selection checkpoint guard and its TaskService policy:\n"
        + "\n".join(violations)
    )


def test_workspace_file_access_has_single_filesystem_owner() -> None:
    imports = backend_imports()
    file_access_path = BACKEND_ROOT / "workspace" / "file_access.py"
    service_path = BACKEND_ROOT / "workspace" / "service.py"
    service_source = service_path.read_text(encoding="utf-8")
    service_tree = ast.parse(service_source, filename=str(service_path))
    service_class = next(
        node
        for node in service_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WorkspaceService"
    )
    service_methods = {
        node.name: node
        for node in service_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    initializer = service_methods["__init__"]
    owners = class_definitions(_python_files(BACKEND_ROOT), "WorkspaceFileAccess")
    service_imports_file_access = tuple(
        record
        for record in imports
        if record.source_module == WORKSPACE_SERVICE_MODULE
        and record.target_module == WORKSPACE_FILE_ACCESS_MODULE
        and "WorkspaceFileAccess" in record.symbols
    )
    file_access_imports_service = tuple(
        record
        for record in imports
        if record.source_module == WORKSPACE_FILE_ACCESS_MODULE
        and record.target_module == WORKSPACE_SERVICE_MODULE
    )
    service_import_nodes = tuple(
        node for node in ast.walk(service_tree) if isinstance(node, (ast.Import, ast.ImportFrom))
    )

    def imported_from(module: str, name: str) -> ast.ImportFrom | None:
        return next(
            (
                node
                for node in service_import_nodes
                if isinstance(node, ast.ImportFrom)
                and node.module == module
                and any(alias.name == name for alias in node.names)
            ),
            None,
        )

    def imported_module(name: str) -> ast.Import | None:
        return next(
            (
                node
                for node in service_import_nodes
                if isinstance(node, ast.Import)
                and any(alias.name == name for alias in node.names)
            ),
            None,
        )

    def delegates_to_file_access(
        method: ast.FunctionDef | ast.AsyncFunctionDef,
        operation: str,
    ) -> bool:
        if len(method.body) != 1 or not isinstance(method.body[0], ast.Return):
            return False
        value = method.body[0].value
        return (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == operation
            and isinstance(value.func.value, ast.Attribute)
            and value.func.value.attr == "file_access"
            and isinstance(value.func.value.value, ast.Name)
            and value.func.value.value.id == "self"
        )

    helper_owners = {
        "_is_reparse": next(
            (
                node
                for node in service_tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "_is_reparse"
            ),
            None,
        ),
        "_thumbnail_path": service_methods.get("_thumbnail_path"),
        "_render_thumbnail": service_methods.get("_render_thumbnail"),
        "_filesystem_roots": service_methods.get("_filesystem_roots"),
    }
    thumbnail_source = ast.get_source_segment(service_source, service_methods["thumbnail"]) or ""
    directories_source = (
        ast.get_source_segment(service_source, service_methods["directories"]) or ""
    )
    initializer_has_file_access = any(
        argument.arg == "file_access" for argument in initializer.args.kwonlyargs
    )
    initializer_constructs_file_access = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "WorkspaceFileAccess"
        for node in ast.walk(initializer)
    )
    violations = []
    if not file_access_path.exists():
        violations.append(
            f"{file_access_path.relative_to(PROJECT_ROOT).as_posix()}: file access module missing"
        )
    if len(owners) != 1 or owners[0].source_module != WORKSPACE_FILE_ACCESS_MODULE:
        details = ", ".join(
            f"{owner.source_path}:{owner.line} -> {owner.source_module}.{owner.name}"
            for owner in owners
        )
        violations.append(f"WorkspaceFileAccess owner mismatch: {details or '<none>'}")
    for helper_name, owner in helper_owners.items():
        if owner is not None:
            violations.append(
                f"backend/dataset_audit_studio/workspace/service.py:{owner.lineno}: "
                f"WorkspaceService still owns {helper_name}"
            )
    if any(
        marker in thumbnail_source
        for marker in (
            "self.database.read_session",
            "LatentSample(",
            "source_is_unchanged(",
            "self._thumbnail_path(",
            "self._render_thumbnail(",
        )
    ):
        violations.append(
            "backend/dataset_audit_studio/workspace/service.py: "
            "WorkspaceService still owns inline thumbnail filesystem behavior"
        )
    if any(
        marker in directories_source
        for marker in (
            "self._filesystem_roots(",
            "_is_reparse(",
            "DirectoryEntryView(",
            "Path(raw_path)",
        )
    ):
        violations.append(
            "backend/dataset_audit_studio/workspace/service.py: "
            "WorkspaceService still owns inline directory filesystem behavior"
        )
    for module_name in ("os", "stat", "string"):
        node = imported_module(module_name)
        if node is not None:
            violations.append(
                f"backend/dataset_audit_studio/workspace/service.py:{node.lineno}: "
                f"exclusive filesystem import {module_name} remains in WorkspaceService"
            )
    for module_name, symbol in (
        ("PIL", "Image"),
        ("PIL", "ImageOps"),
        ("dataset_audit_studio.latent.common", "source_is_unchanged"),
        ("dataset_audit_studio.latent.types", "LatentSample"),
    ):
        node = imported_from(module_name, symbol)
        if node is not None:
            violations.append(
                f"backend/dataset_audit_studio/workspace/service.py:{node.lineno}: "
                f"exclusive filesystem import {module_name}.{symbol} remains in WorkspaceService"
            )
    if not service_imports_file_access:
        violations.append(
            "backend/dataset_audit_studio/workspace/service.py: "
            "WorkspaceFileAccess import missing"
        )
    if not initializer_has_file_access:
        violations.append(
            "backend/dataset_audit_studio/workspace/service.py: "
            "file_access injection missing"
        )
    if not initializer_constructs_file_access:
        violations.append(
            "backend/dataset_audit_studio/workspace/service.py: "
            "WorkspaceFileAccess default construction missing"
        )
    for method_name in ("thumbnail", "directories"):
        if not delegates_to_file_access(service_methods[method_name], method_name):
            violations.append(
                f"backend/dataset_audit_studio/workspace/service.py:"
                f"{service_methods[method_name].lineno}: "
                f"WorkspaceService {method_name} delegation missing"
            )
    violations.extend(
        f"file access reverse dependency {_format_import(record)}"
        for record in file_access_imports_service
    )
    assert not violations, (
        "workspace filesystem access must have one workspace file owner:\n"
        + "\n".join(violations)
    )


def test_export_tree_publisher_has_single_export_owner() -> None:
    imports = backend_imports()
    publisher_path = BACKEND_ROOT / "export" / "tree_publisher.py"
    service_path = BACKEND_ROOT / "export" / "service.py"
    service_source = service_path.read_text(encoding="utf-8")
    service_tree = ast.parse(service_source, filename=str(service_path))
    exporter = next(
        node
        for node in service_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DatasetExporter"
    )
    methods = {
        node.name: node
        for node in exporter.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    initializer = methods["__init__"]
    publisher_owners = class_definitions(
        _python_files(BACKEND_ROOT), "ExportTreePublisher"
    )
    service_imports_publisher = tuple(
        record
        for record in imports
        if record.source_module == EXPORT_SERVICE_MODULE
        and record.target_module == TREE_PUBLISHER_MODULE
        and "ExportTreePublisher" in record.symbols
    )
    publisher_reverse_imports = tuple(
        record
        for record in imports
        if record.source_module == TREE_PUBLISHER_MODULE
        and record.target_module == EXPORT_SERVICE_MODULE
    )
    service_import_nodes = tuple(
        node
        for node in ast.walk(service_tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    )
    publisher_source = (
        publisher_path.read_text(encoding="utf-8") if publisher_path.exists() else ""
    )
    publisher_tree = (
        ast.parse(publisher_source, filename=str(publisher_path))
        if publisher_source
        else None
    )
    publisher_class = (
        next(
            (
                node
                for node in publisher_tree.body
                if isinstance(node, ast.ClassDef) and node.name == "ExportTreePublisher"
            ),
            None,
        )
        if publisher_tree is not None
        else None
    )
    publisher_methods = {
        node.name
        for node in publisher_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    } if publisher_class is not None else set()
    publisher_forbidden_imports = tuple(
        record
        for record in imports
        if record.source_module == TREE_PUBLISHER_MODULE
        and record.target_module.startswith(
            (
                "dataset_audit_studio.adapters.json_artifact_store",
                "dataset_audit_studio.database",
                "dataset_audit_studio.export.repository",
                "dataset_audit_studio.export.rewrite",
                "dataset_audit_studio.jobs",
            )
        )
    )

    def preserves_exact_injected_publisher() -> bool:
        for statement in initializer.body:
            if not (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Attribute)
                and isinstance(statement.targets[0].value, ast.Name)
                and statement.targets[0].value.id == "self"
                and statement.targets[0].attr == "tree_publisher"
                and isinstance(statement.value, ast.IfExp)
            ):
                continue
            condition = statement.value.test
            return (
                isinstance(condition, ast.Compare)
                and isinstance(condition.left, ast.Name)
                and condition.left.id == "tree_publisher"
                and len(condition.ops) == 1
                and isinstance(condition.ops[0], ast.IsNot)
                and len(condition.comparators) == 1
                and isinstance(condition.comparators[0], ast.Constant)
                and condition.comparators[0].value is None
                and isinstance(statement.value.body, ast.Name)
                and statement.value.body.id == "tree_publisher"
                and isinstance(statement.value.orelse, ast.Call)
                and isinstance(statement.value.orelse.func, ast.Name)
                and statement.value.orelse.func.id == "ExportTreePublisher"
            )
        return False

    violations = []
    if not publisher_path.exists():
        violations.append(
            f"{publisher_path.relative_to(PROJECT_ROOT).as_posix()}: tree publisher module missing"
        )
    if (
        len(publisher_owners) != 1
        or publisher_owners[0].source_module != TREE_PUBLISHER_MODULE
    ):
        details = ", ".join(
            f"{owner.source_path}:{owner.line} -> {owner.source_module}.{owner.name}"
            for owner in publisher_owners
        )
        violations.append(f"ExportTreePublisher owner mismatch: {details or '<none>'}")
    required_publisher_methods = {
        "validate_roots",
        "prepare_directories",
        "assert_staging_ready",
        "write_file",
        "verify_file",
        "verify_tree_layout",
        "verify_tree",
        "publish_tree",
        "publish_bytes",
    }
    missing_publisher_methods = sorted(required_publisher_methods - publisher_methods)
    if missing_publisher_methods:
        violations.append(
            "ExportTreePublisher missing methods: "
            + ", ".join(missing_publisher_methods)
        )
    for method_name in (
        "_validate_roots",
        "_prepare_directories",
        "_destination",
        "_write_file",
        "_copy_source_file",
        "_write_content_file",
        "_publish_part",
        "_verify_file",
        "_verify_tree_layout",
        "_verify_tree",
    ):
        if method_name in methods:
            violations.append(
                f"backend/dataset_audit_studio/export/service.py: "
                f"DatasetExporter still owns {method_name}"
            )
    if service_imports_publisher:
        violations.append(
            "backend/dataset_audit_studio/export/service.py: rewrite service still imports "
            "the copy tree publisher"
        )
    if any(argument.arg == "tree_publisher" for argument in initializer.args.kwonlyargs):
        violations.append(
            "backend/dataset_audit_studio/export/service.py: tree_publisher injection missing"
        )
    if preserves_exact_injected_publisher():
        violations.append(
            "backend/dataset_audit_studio/export/service.py: "
            "rewrite service must not construct or retain a copy tree publisher"
        )
    for module_name in ("os", "shutil", "time"):
        if any(
            isinstance(node, ast.Import)
            and any(alias.name == module_name for alias in node.names)
            for node in service_import_nodes
        ):
            violations.append(
                f"backend/dataset_audit_studio/export/service.py: "
                f"low-level import {module_name} remains"
            )
    for module_name, symbol in (
        ("collections.abc", "Callable"),
        ("typing", "TypeVar"),
        ("dataset_audit_studio.core.file_integrity", "is_reparse"),
        ("dataset_audit_studio.core.file_integrity", "sha256_file"),
    ):
        if any(
            isinstance(node, ast.ImportFrom)
            and node.module == module_name
            and any(alias.name == symbol for alias in node.names)
            for node in service_import_nodes
        ):
            violations.append(
                f"backend/dataset_audit_studio/export/service.py: "
                f"low-level import {module_name}.{symbol} remains"
            )
    for method_name in (
        "_run_rewrite",
        "_complete_or_control",
        "_commit_control",
    ):
        if method_name not in methods:
            violations.append(
                f"backend/dataset_audit_studio/export/service.py: "
                f"DatasetExporter must retain {method_name}"
            )
    violations.extend(
        f"tree publisher reverse dependency {_format_import(record)}"
        for record in publisher_reverse_imports
    )
    violations.extend(
        f"tree publisher forbidden dependency {_format_import(record)}"
        for record in publisher_forbidden_imports
    )
    if any(marker in publisher_source for marker in ("importlib", "__import__", "__getattr__")):
        violations.append("ExportTreePublisher contains a dynamic import bypass")
    assert not violations, (
        "copy export tree publishing must have one low-level owner:\n"
        + "\n".join(violations)
    )


def test_profile_contract_callers_do_not_import_the_preset_facade() -> None:
    callers = facade_callers(backend_and_test_imports())
    details = "\n".join(
        f"{caller.source_path}:{caller.line} -> "
        f"{caller.target_module}.{caller.symbol}"
        for caller in callers
    )
    assert not callers, (
        "Canonical profile callers must import dataset_audit_studio.core.profile_contracts "
        f"instead of presets.builtin:\n{details}"
    )


def test_preset_does_not_statically_import_app() -> None:
    reverse_edges = tuple(
        record
        for record in backend_imports()
        if record.source_module == PRESET_MODULE
        and record.target_module.startswith("dataset_audit_studio.app")
    )
    details = "\n".join(_format_import(record) for record in reverse_edges)
    assert not reverse_edges, (
        "presets.builtin must not statically import app composition modules:\n"
        f"{details}"
    )


def test_materialize_profile_callers_do_not_import_the_preset_owner() -> None:
    callers = tuple(
        record
        for record in backend_and_test_imports()
        if record.target_module == PRESET_MODULE
        and MATERIALIZE_PROFILE_NAME in record.symbols
    )
    details = "\n".join(_format_import(record) for record in callers)
    assert not callers, (
        "materialize_profile callers must import the app composition owner instead of "
        f"presets.builtin:\n{details}"
    )


def test_materialize_profile_has_the_single_app_composition_owner() -> None:
    owners = function_definitions(_python_files(BACKEND_ROOT), MATERIALIZE_PROFILE_NAME)
    details = "\n".join(
        f"{owner.source_path}:{owner.line} -> {owner.source_module}.{owner.name}"
        for owner in owners
    )
    assert len(owners) == 1 and owners[0].source_module == MATERIALIZATION_MODULE, (
        "materialize_profile must have exactly one app composition owner:\n"
        f"{details}"
    )
