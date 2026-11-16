import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { latticeApi } from "@/api/client";

export function useSkillsCatalog() {
  const qc = useQueryClient();
  const skills = useQuery({ queryKey: ["skills"], queryFn: latticeApi.skills });
  const market = useQuery({ queryKey: ["skillsMarketplace"], queryFn: latticeApi.skillsMarketplace });
  const install = useMutation({
    mutationFn: (skill: Record<string, unknown>) =>
      latticeApi.skillInstall(String(skill.name || skill.id), String(skill.plugin || "")),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }),
  });
  return { skills, market, install };
}

export function useMcpCatalog() {
  const [query, setQuery] = React.useState("github");
  const tools = useQuery({ queryKey: ["mcpTools"], queryFn: latticeApi.mcpTools });
  const rec = useMutation({ mutationFn: () => latticeApi.mcpRecommend(query) });
  return { query, setQuery, tools, rec };
}

export function useMarketplaceCatalog() {
  const templates = useQuery({ queryKey: ["templates"], queryFn: latticeApi.templates });
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: latticeApi.pluginsRegistry });
  const dir = useQuery({ queryKey: ["pluginsDirectory"], queryFn: latticeApi.pluginsDirectory });
  return { templates, plugins, dir };
}
