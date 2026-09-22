from abc import ABC, abstractmethod

from src.content.table.models import Table
from src.content.table.schemas import ProjectWithTables


class TableRepositoryBase(ABC):
    """Abstract Table repository interface"""

    @abstractmethod
    def get_by_org_id(self, org_id: str) -> list[Table]:
        """Get all Tables by organization ID (via project association)"""

    @abstractmethod
    def get_projects_with_tables_by_org_id(
        self, org_id: str
    ) -> list[ProjectWithTables]:
        """Get all projects and their tables for an organization"""

    @abstractmethod
    def get_by_id(self, table_id: str) -> Table | None:
        pass

    @abstractmethod
    def update(
        self,
        table_id: str,
        name: str | None,
        description: str | None,
        data: dict | None,
    ) -> Table | None:
        pass

    @abstractmethod
    def delete(self, table_id: str) -> bool:
        pass

    @abstractmethod
    def create(
        self,
        created_by: str,
        name: str,
        description: str,
        data: dict,
        project_id: str,
    ) -> Table:
        pass

    @abstractmethod
    def update_context_data(self, table_id: str, data: dict) -> Table | None:
        """Update the data field"""


class TableRepositorySupabase(TableRepositoryBase):
    """Supabase-based Table repository implementation"""

    def __init__(self, supabase_repo=None):
        """
        Initialize repository.

        Args:
            supabase_repo: Optional SupabaseRepository instance; creates a new one if not provided
        """
        if supabase_repo is None:
            # Lazy import to avoid triggering during module import
            from src.infra.supabase.dependencies import get_supabase_repository

            # Use shared singleton instance to avoid duplicate creation
            self._supabase_repo = get_supabase_repository()
        else:
            self._supabase_repo = supabase_repo

    def get_by_org_id(self, org_id: str) -> list[Table]:
        """
        Get all Tables by organization ID (via project association).

        Args:
            org_id: Organization ID

        Returns:
            List of Tables
        """
        projects = self._supabase_repo.get_projects(org_id=org_id)
        project_ids = [project.id for project in projects]

        if not project_ids:
            return []

        all_tables = []
        for project_id in project_ids:
            tables = self._supabase_repo.get_tables(project_id=project_id)
            all_tables.extend(tables)

        return [self._table_response_to_table(table) for table in all_tables]

    def get_projects_with_tables_by_org_id(
        self, org_id: str
    ) -> list[ProjectWithTables]:
        """
        Get all projects and their tables for an organization.

        Args:
            org_id: Organization ID

        Returns:
            List containing project info and all their tables
        """
        from src.content.table.schemas import TableOut

        projects = self._supabase_repo.get_projects(org_id=org_id)

        result = []
        for project in projects:
            tables_response = self._supabase_repo.get_tables(project_id=project.id)

            tables = [
                TableOut(
                    id=table.id,
                    name=table.name,
                    project_id=table.project_id,
                    description=table.description,
                    data=table.data,
                    created_at=table.created_at,
                )
                for table in tables_response
            ]

            project_with_tables = ProjectWithTables(
                id=project.id,
                name=project.name,
                description=project.description,
                org_id=project.org_id,
                created_by=project.created_by,
                created_at=project.created_at,
                tables=tables,
            )
            result.append(project_with_tables)

        return result

    def get_by_id(self, table_id: str) -> Table | None:
        """
        Get Table by ID.

        Args:
            table_id: Table ID

        Returns:
            Table object, or None if not found
        """
        table_response = self._supabase_repo.get_table(table_id)
        if table_response:
            return self._table_response_to_table(table_response)
        return None

    def create(
        self,
        created_by: str,
        name: str,
        description: str,
        data: dict,
        project_id: str,
        *,
        table_id: str | None = None,
    ) -> Table:
        """
        Create a new Table.

        Args:
            created_by: Creator user ID (required)
            name: Table name
            description: Table description
            data: Table data (JSON object)
            project_id: Project ID (required)
            table_id: Optional pre-generated ID (e.g. from hash write)

        Returns:
            Created Table object
        """
        from src.content.table.supabase_schemas import TableCreate
        from src.utils.id_generator import generate_uuid_v7

        table_data = TableCreate(
            id=table_id or generate_uuid_v7(),
            name=name,
            project_id=project_id,
            created_by=created_by,
            description=description,
            data=data,
        )
        table_response = self._supabase_repo.create_table(table_data)
        return self._table_response_to_table(table_response)

    def update(
        self,
        table_id: str,
        name: str | None,
        description: str | None,
        data: dict | None,
    ) -> Table | None:
        """
        Update a Table.

        Args:
            table_id: Table ID
            name: Table name (optional, not updated if None)
            description: Table description (optional, not updated if None)
            data: Table data (optional, not updated if None)

        Returns:
            Updated Table object, or None if not found
        """
        from src.content.table.supabase_schemas import TableUpdate

        update_data = TableUpdate(
            name=name,
            description=description,
            data=data,
        )
        table_response = self._supabase_repo.update_table(table_id, update_data)
        if table_response:
            return self._table_response_to_table(table_response)
        return None

    def delete(self, table_id: str) -> bool:
        """
        Delete a Table.

        Args:
            table_id: Table ID

        Returns:
            Whether deletion was successful
        """
        return self._supabase_repo.delete_table(table_id)

    def update_context_data(self, table_id: str, data: dict) -> Table | None:
        """
        Update the data field of a Table.

        Args:
            table_id: Table ID
            data: New data

        Returns:
            Updated Table object, or None if not found
        """
        from src.content.table.supabase_schemas import TableUpdate

        update_data = TableUpdate(data=data)
        table_response = self._supabase_repo.update_table(table_id, update_data)
        if table_response:
            return self._table_response_to_table(table_response)
        return None

    def _table_response_to_table(self, table_response) -> Table:
        """
        Convert TableResponse to Table model.

        Args:
            table_response: TableResponse object

        Returns:
            Table object
        """
        return Table(
            id=table_response.id,
            name=table_response.name,
            project_id=table_response.project_id,
            created_by=table_response.created_by,
            description=table_response.description,
            data=table_response.data,
            created_at=table_response.created_at,
        )
