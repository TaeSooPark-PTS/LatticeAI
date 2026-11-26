const KINDS = [
  { type: "Document", title: "설계 문서", count: 34 },
  { type: "File", title: "작업 파일", count: 30 },
  { type: "Decision", title: "결정 기록", count: 18 },
  { type: "Task", title: "할 일", count: 16 },
  { type: "Concept", title: "개념", count: 40 },
  { type: "Topic", title: "주제", count: 24 },
  { type: "Note", title: "메모", count: 22 },
  { type: "Person", title: "참여자", count: 8 },
];

function grownBrain(baseNodes) {
  const nodes = [];
  const edges = [];
  const hubs = new Map();
  let serial = 0;
  for (const kind of KINDS) {
    const ids = [];
    for (let index = 0; index < kind.count; index += 1) {
      serial += 1;
      const id = `grown:${kind.type.toLowerCase()}:${index}`;
      const superseded = serial % 23 === 0;
      nodes.push({
        id,
        type: kind.type,
        title: `${kind.title} ${index + 1}`,
        summary: "Sample memory used to show a grown Brain",
        importance_norm: 0.2 + ((serial * 7) % 25) / 100,
        metadata: {
          lifecycle_status: superseded ? "superseded" : "active",
          graph_metrics: { degree: 1 + (serial % 4), importance_norm: 0.2 + ((serial * 7) % 25) / 100 },
        },
      });
      ids.push(id);
      edges.push({ from: index === 0 ? baseNodes[serial % baseNodes.length].id : ids[Math.floor((index - 1) / 3)], to: id, type: "mentions", weight: 0.8 });
      if (hubs.size && serial % 5 === 0) {
        const others = Array.from(hubs.values());
        edges.push({ from: others[serial % others.length], to: id, type: "discusses", weight: 0.7 });
      }
    }
    hubs.set(kind.type, ids[0]);
  }
  return { nodes, edges };
}

module.exports = { grownBrain };
