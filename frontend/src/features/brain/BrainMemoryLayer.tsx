import type { BrainDepth, MemoryFragment } from "./types";
import { t } from "@/i18n";
import { useAppStore } from "@/store/appStore";
import { layerStyle, polarPoint } from "./graphLayout";

export function BrainMemoryLayer({
  memories,
  depth,
  onRecallMemory,
}: {
  memories: MemoryFragment[];
  depth: BrainDepth;
  onRecallMemory: (fragment: MemoryFragment) => void;
}) {
  const language = useAppStore((state) => state.language);
  const visible = memories.slice(0, depth >= 3 ? 8 : 6);
  if (!visible.length) {
    return (
      <div className="memory-fragment is-empty">
        <span>{t(language, "brain.memory.empty.kicker")}</span>
        <strong>{t(language, "brain.memory.empty")}</strong>
      </div>
    );
  }

  return (
    <>
      {visible.map((memory, index) => {
        const point = polarPoint(index, visible.length, depth >= 3 ? 39 : 31, depth >= 3 ? 24 : 18, -112);
        return (
          <button
            key={memory.id}
            type="button"
            className="memory-fragment"
            style={layerStyle({ "--x": `${point.x}%`, "--y": `${point.y}%`, "--delay": `${index * 55}ms` })}
            onClick={() => onRecallMemory(memory)}
          >
            <span>{memory.kind}</span>
            <strong>{memory.title}</strong>
          </button>
        );
      })}
    </>
  );
}
