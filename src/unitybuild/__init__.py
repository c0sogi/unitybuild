"""Unity build APIs; importing this package performs no project selection or I/O."""

from .builds import BuildResult, build_project
from .profiles import BuildProfile, SavedBuildProfile, discover_build_profiles, resolve_build_profile
from .projects import Project, ProjectCatalog, ProjectError, discover_projects

__all__ = [
    "BuildProfile",
    "BuildResult",
    "Project",
    "ProjectCatalog",
    "ProjectError",
    "SavedBuildProfile",
    "build_project",
    "discover_build_profiles",
    "discover_projects",
    "resolve_build_profile",
]
