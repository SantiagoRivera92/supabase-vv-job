import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';

interface Card {
  oracle_id: string;
  name: string;
  legalities: { vintage: string };
  prices: { usd?: string; usd_foil?: string; usd_etched?: string };
  edhrec_rank?: number;
}

async function runUpdate() {
  console.log("--- Starting Card Update ---");
  
  const supabaseUrl = Deno.env.get('SUPABASE_URL');
  const supabaseServiceKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');

  if (!supabaseUrl || !supabaseServiceKey) {
    throw new Error("Missing environment variables SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY");
  }

  const supabase = createClient(supabaseUrl, supabaseServiceKey);

  // 1. Get Bulk Metadata
  console.log("Fetching Scryfall metadata...");
  const bulkRes = await fetch('https://api.scryfall.com/bulk-data');
  const bulkData = await bulkRes.json();
  const target = bulkData.data.find((item: any) => item.type === 'default_cards');

  if (!target) throw new Error("Could not find default_cards in Scryfall API.");
  console.log(`Target file: ${target.updated_at}`);

  // 2. Check if already processed
  const { data: existing } = await supabase
    .from('updates')
    .select('filename')
    .eq('filename', target.updated_at)
    .maybeSingle();

  if (existing) {
    console.log("File already processed. Skipping.");
    return;
  }

  // 3. Download & Parse (7GB RAM handles this easily)
  console.log("Downloading 500MB+ Scryfall data...");
  const cardsRes = await fetch(target.download_uri);
  const cardsData: Card[] = await cardsRes.json();
  console.log(`Processing ${cardsData.length} cards...`);

  // 4. Transform Data
  const cardDict: Record<string, any> = {};
  const today = new Date().toISOString().split('T')[0];

  for (const card of cardsData) {
    if (card.legalities?.vintage === 'not_legal' || !card.oracle_id) continue;

    const prices = [card.prices?.usd, card.prices?.usd_foil, card.prices?.usd_etched]
      .filter((p): p is string => !!p)
      .map(p => parseFloat(p));

    if (prices.length === 0) continue;
    const minPrice = Math.min(...prices);

    if (!(card.oracle_id in cardDict) || cardDict[card.oracle_id].price > minPrice) {
      cardDict[card.oracle_id] = {
        oracle_id: card.oracle_id,
        name: card.name,
        edhrec_rank: card.edhrec_rank,
        price: minPrice,
        date: today
      };
    }
  }

  // 5. Bulk Upsert in Batches
  const entries = Object.values(cardDict);
  const batchSize = 1000;
  console.log(`Upserting ${entries.length} cards in batches of ${batchSize}...`);

  for (let i = 0; i < entries.length; i += batchSize) {
    const batch = entries.slice(i, i + batchSize);
    
    // Upsert Card Info
    await supabase.from('cards').upsert(batch.map(c => ({
      oracle_id: c.oracle_id,
      name: c.name,
      edhrec_rank: c.edhrec_rank
    })));

    // Insert Price Info
    await supabase.from('prices').insert(batch.map(c => ({
      oracle_id: c.oracle_id,
      price: c.price,
      date: c.date
    })));

    if (i % 5000 === 0) console.log(`Progress: ${i} / ${entries.length}`);
  }

  // 6. Record Success
  await supabase.from('updates').insert({ filename: target.updated_at });
  console.log("--- Update Finished Successfully ---");
}

runUpdate().catch(err => {
  console.error("FAILED:", err);
  Deno.exit(1);
});