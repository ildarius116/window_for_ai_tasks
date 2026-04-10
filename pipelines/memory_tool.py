"""
title: MWS Memory Manager
author: MWS GPT
version: 1.0.0
description: Tool for users to view, search, and manage their long-term memories stored in MWS Memory Service.
"""

import requests
from pydantic import BaseModel, Field
from typing import Optional


class Tools:
    class Valves(BaseModel):
        MEMORY_SERVICE_URL: str = Field(
            default="http://memory-service:8000",
            description="Memory Service base URL",
        )

    def __init__(self):
        self.valves = self.Valves()

    def list_memories(
        self,
        __user__: dict = {},
    ) -> str:
        """
        List all stored memories for the current user. Use this when the user asks
        to see their memories, what you remember about them, or wants to review
        stored information.
        """
        user_id = __user__.get("id", "")
        if not user_id:
            return "Error: could not determine user ID."

        try:
            resp = requests.get(
                f"{self.valves.MEMORY_SERVICE_URL}/memories/{user_id}",
                timeout=5,
            )
            if resp.status_code == 200:
                memories = resp.json()
                if not memories:
                    return "No memories stored yet."
                lines = []
                for i, m in enumerate(memories, 1):
                    created = m.get("created_at", "unknown")[:10]
                    content = m.get("content", "")
                    mid = m.get("id", "")[:8]
                    lines.append(f"{i}. [{created}] {content} (id: {mid}...)")
                return f"Found {len(memories)} memories:\n" + "\n".join(lines)
            else:
                return f"Error fetching memories: {resp.status_code}"
        except Exception as e:
            return f"Error connecting to Memory Service: {e}"

    def search_memories(
        self,
        query: str,
        __user__: dict = {},
    ) -> str:
        """
        Search memories by semantic similarity. Use this when the user asks about
        specific topics or wants to find relevant stored information.

        :param query: The search query to find relevant memories.
        """
        user_id = __user__.get("id", "")
        if not user_id:
            return "Error: could not determine user ID."

        try:
            resp = requests.post(
                f"{self.valves.MEMORY_SERVICE_URL}/memories/search",
                json={"user_id": user_id, "query": query, "limit": 5},
                timeout=5,
            )
            if resp.status_code == 200:
                results = resp.json()
                if not results:
                    return f"No memories found matching '{query}'."
                lines = []
                for i, r in enumerate(results, 1):
                    content = r.get("content", "")
                    lines.append(f"{i}. {content}")
                return f"Found {len(results)} relevant memories:\n" + "\n".join(lines)
            else:
                return f"Error searching memories: {resp.status_code}"
        except Exception as e:
            return f"Error connecting to Memory Service: {e}"

    def delete_memory(
        self,
        memory_id: str,
        __user__: dict = {},
    ) -> str:
        """
        Delete a specific memory by its ID. Use this when the user asks to forget
        something or delete a specific memory. Get the ID from list_memories first.

        :param memory_id: The UUID of the memory to delete.
        """
        try:
            resp = requests.delete(
                f"{self.valves.MEMORY_SERVICE_URL}/memories/{memory_id}",
                timeout=5,
            )
            if resp.status_code == 200:
                return f"Memory {memory_id} deleted successfully."
            elif resp.status_code == 404:
                return f"Memory {memory_id} not found."
            else:
                return f"Error deleting memory: {resp.status_code}"
        except Exception as e:
            return f"Error connecting to Memory Service: {e}"

    def clear_all_memories(
        self,
        __user__: dict = {},
    ) -> str:
        """
        Delete ALL memories for the current user. Use this only when the user
        explicitly asks to clear/delete all their memories or start fresh.
        """
        user_id = __user__.get("id", "")
        if not user_id:
            return "Error: could not determine user ID."

        try:
            resp = requests.delete(
                f"{self.valves.MEMORY_SERVICE_URL}/memories/user/{user_id}",
                timeout=5,
            )
            if resp.status_code == 200:
                return "All memories cleared successfully."
            else:
                return f"Error clearing memories: {resp.status_code}"
        except Exception as e:
            return f"Error connecting to Memory Service: {e}"
