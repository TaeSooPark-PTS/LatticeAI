use std::collections::{BTreeMap, HashSet};

use lattice_retrieval::native_hnsw::NativeHnswGraph;

const DIM: usize = 32;
const CLUSTERS: usize = 24;
const INITIAL: usize = 1_500;
const ROUNDS: usize = 6;
const QUERIES: usize = 100;
const K: usize = 10;
const RECALL_FLOOR: f64 = 0.90;

struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    fn below(&mut self, bound: usize) -> usize {
        (self.next() % bound as u64) as usize
    }

    fn unit(&mut self) -> f32 {
        (self.next() >> 40) as f32 / (1u64 << 24) as f32 * 2.0 - 1.0
    }
}

fn normalized(mut vector: Vec<f32>) -> Vec<f32> {
    let norm = vector.iter().map(|v| v * v).sum::<f32>().sqrt();
    vector.iter_mut().for_each(|v| *v /= norm);
    vector
}

fn sample(rng: &mut Rng, centers: &[Vec<f32>]) -> Vec<f32> {
    let center = &centers[rng.below(centers.len())];
    normalized(center.iter().map(|c| c + 0.6 * rng.unit()).collect())
}

fn exact_top_k(corpus: &BTreeMap<String, Vec<f32>>, query: &[f32]) -> HashSet<String> {
    let mut scored: Vec<(f32, &String)> = corpus
        .iter()
        .map(|(id, vector)| {
            let score = vector.iter().zip(query).map(|(a, b)| a * b).sum::<f32>();
            (score, id)
        })
        .collect();
    scored.sort_by(|a, b| b.0.total_cmp(&a.0));
    scored.iter().take(K).map(|(_, id)| (*id).clone()).collect()
}

fn recall(
    graph: &NativeHnswGraph,
    corpus: &BTreeMap<String, Vec<f32>>,
    rng: &mut Rng,
    centers: &[Vec<f32>],
) -> f64 {
    let mut found = 0;
    for _ in 0..QUERIES {
        let query = sample(rng, centers);
        let wide: Vec<f64> = query.iter().map(|v| f64::from(*v)).collect();
        let hits = graph.query(&wide, K);
        assert!(hits.iter().all(|id| corpus.contains_key(id)));
        assert_eq!(hits.iter().collect::<HashSet<_>>().len(), hits.len());
        let truth = exact_top_k(corpus, &query);
        found += hits.iter().filter(|id| truth.contains(*id)).count();
    }
    found as f64 / (QUERIES * K) as f64
}

struct Churn {
    graph: NativeHnswGraph,
    corpus: BTreeMap<String, Vec<f32>>,
    rng: Rng,
    centers: Vec<Vec<f32>>,
    serial: usize,
    version: usize,
}

impl Churn {
    fn new(seed: u64) -> Self {
        let mut rng = Rng(seed);
        let centers = (0..CLUSTERS)
            .map(|_| normalized((0..DIM).map(|_| rng.unit()).collect()))
            .collect();
        Self {
            graph: NativeHnswGraph::new(DIM),
            corpus: BTreeMap::new(),
            rng,
            centers,
            serial: 0,
            version: 0,
        }
    }

    fn put(&mut self, id: String) {
        let vector = sample(&mut self.rng, &self.centers);
        self.version += 1;
        self.graph
            .upsert(id.clone(), format!("h{}", self.version), &vector);
        self.corpus.insert(id, vector);
    }

    fn insert(&mut self, count: usize) {
        for _ in 0..count {
            self.serial += 1;
            self.put(format!("item-{:06}", self.serial));
        }
    }

    fn pick(&mut self, count: usize) -> Vec<String> {
        let mut ids: Vec<String> = self.corpus.keys().cloned().collect();
        for index in 0..count {
            let other = index + self.rng.below(ids.len() - index);
            ids.swap(index, other);
        }
        ids.truncate(count);
        ids
    }

    fn round(&mut self) -> bool {
        let removed = self.pick(self.corpus.len() * 3 / 10);
        for id in &removed {
            self.corpus.remove(id);
        }
        let present: HashSet<String> = self.corpus.keys().cloned().collect();
        let wants_rebuild = self.graph.remove_missing(&present);
        let revised = self.pick(self.corpus.len() / 10);
        for id in revised {
            self.put(id);
        }
        let revived = removed.len() / 3;
        for id in removed.into_iter().take(revived) {
            self.put(id);
        }
        let shortfall = INITIAL.saturating_sub(self.corpus.len());
        self.insert(shortfall);
        assert_eq!(self.graph.active_len(), self.corpus.len());
        wants_rebuild
    }

    fn rebuild(&mut self) {
        self.graph = NativeHnswGraph::new(DIM);
        for (id, vector) in &self.corpus {
            self.graph.upsert(id.clone(), "rebuilt".into(), vector);
        }
    }

    fn measure(&mut self) -> f64 {
        recall(&self.graph, &self.corpus, &mut self.rng, &self.centers)
    }
}

fn run(seed: u64, honour_rebuild: bool) -> Vec<f64> {
    let mut churn = Churn::new(seed);
    churn.insert(INITIAL);
    let mut measured = vec![churn.measure()];
    for _ in 0..ROUNDS {
        if churn.round() && honour_rebuild {
            churn.rebuild();
        }
        measured.push(churn.measure());
    }
    println!("seed {seed:#x} rebuild={honour_rebuild} recall@{K}: {measured:?}");
    measured
}

#[test]
fn recall_survives_churn_when_requested_rebuilds_are_honoured() {
    for (round, value) in run(0x1A77_1CE5, true).into_iter().enumerate() {
        assert!(value >= RECALL_FLOOR, "round {round}: recall {value}");
    }
}

#[test]
fn recall_survives_churn_on_accumulated_tombstones() {
    for (round, value) in run(0x1A77_1CE5, false).into_iter().enumerate() {
        assert!(value >= RECALL_FLOOR, "round {round}: recall {value}");
    }
}
