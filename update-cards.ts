import { createClient } from 'https://esm.sh/@supabase/supabase-js@2.91.0';

const weighs = JSON.parse(await Deno.readTextFile('weighs.json'));
const prioritize = new Set(weighs.prioritize);
const deprioritize = new Set(weighs.deprioritize);

interface Card {
  oracle_id: string;
  name: string;
  legalities: { vintage: string };
  prices: { usd?: string; usd_foil?: string; usd_etched?: string };
  edhrec_rank?: number;
  set_name: string;
  tcgplayer_id?: number;
  set_type: string;
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

  // 3. Download & Parse
  console.log("Downloading 500MB+ Scryfall data...");
  const cardsRes = await fetch(target.download_uri);
  const cardsData: Card[] = await cardsRes.json();
  console.log(`Processing ${cardsData.length} cards...`);

  // 4. Transform Data
  const cardDict: Record<string, any> = {};
  const now = new Date().toISOString();

  for (const card of cardsData) {
    // Basic Filters
    if (card.legalities?.vintage === 'not_legal' || !card.oracle_id) continue;

    if (card.set_name === "Summer Magic / Edgar" || card.set_type === "memorabilia") {
      console.log("Skipping", card.name, "from set ", card.set_name);
      continue; 
    }

    // Price Calculation: only consider prices > 0.01
    const prices = [card.prices?.usd, card.prices?.usd_foil, card.prices?.usd_etched]
      .filter((p): p is string => !!p)
      .map(p => parseFloat(p))
      .filter(price => price > 0.01);

    if (prices.length === 0) continue;
    const minPrice = Math.min(...prices);

    // Cheapest Printing Logic
    if (!(card.oracle_id in cardDict) || cardDict[card.oracle_id].price > minPrice) {
      let adjustedRank = card.edhrec_rank;
      
      // Determine Boolean flags from weighs
      const isStaple = prioritize.has(card.name);
      const isDisincentivized = deprioritize.has(card.name);

      if (adjustedRank !== undefined) {
        if (isStaple) {
          adjustedRank = Math.max(1, Math.floor(adjustedRank / 1000));
        } else if (isDisincentivized) {
          adjustedRank = Math.floor(adjustedRank * 10000);
        }
      }

      cardDict[card.oracle_id] = {
        oracle_id: card.oracle_id,
        name: card.name,
        edhrec_rank: adjustedRank,
        tcgplayer_id: card.tcgplayer_id,
        price: minPrice,
        date: now,
        is_staple: isStaple,
        is_disincentivized: isDisincentivized
      };
    }
  }

  // 5. Record the update first to satisfy foreign key
  try {
    const { error: updatesError } = await supabase.from('updates').insert({ filename: target.updated_at });
    if (updatesError) {
      console.error(`Error recording update: ${updatesError.message}`);
      return; 
    } else {
      console.log("Successfully recorded update.");
    }
  } catch (err) {
    console.error(`Exception recording update: ${err}`);
    return;
  }

  // 6. Bulk Upsert in Batches
  const entries = Object.values(cardDict);
  const batchSize = 1000;
  console.log(`Upserting ${entries.length} cards in batches of ${batchSize}...`);

  for (let i = 0; i < entries.length; i += batchSize) {
    const batch = entries.slice(i, i + batchSize);
    
    // Upsert Card Info (Including new columns)
    try {
      const { error: cardsError } = await supabase.from('cards').upsert(batch.map(c => ({
        oracle_id: c.oracle_id,
        name: c.name,
        edhrec_rank: c.edhrec_rank,
        tcgplayer_id: c.tcgplayer_id,
        is_staple: c.is_staple,
        is_disincentivized: c.is_disincentivized
      })));
      
      if (cardsError) {
        console.error(`Error upserting cards: ${cardsError.message}`);
      } else {
        console.log(`Successfully upserted ${batch.length} cards.`);
      }
    } catch (err) {
      console.error(`Exception upserting cards: ${err}`);
    }

    // Insert Price Info
    try {
      const { error: pricesError } = await supabase.from('prices').insert(batch.map(c => ({
        oracle_id: c.oracle_id,
        price: c.price,
        date: c.date,
        filename: target.updated_at
      })));
      if (pricesError) {
        console.error(`Error inserting prices: ${pricesError.message}`);
      } else {
        console.log(`Successfully inserted ${batch.length} price entries.`);
      }
    } catch (err) {
      console.error(`Exception inserting prices: ${err}`);
    }

    if (i % 5000 === 0) console.log(`Progress: ${i} / ${entries.length}`);
  }

  // 7. Cleanup: Remove prices and updates older than one week
  try {
    const oneWeekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
    const { data: oldUpdates, error: oldUpdatesError } = await supabase
      .from('updates')
      .select('filename')
      .lt('filename', oneWeekAgo);

    if (oldUpdatesError) {
      console.error('Error fetching old updates:', oldUpdatesError.message);
    } else if (oldUpdates && oldUpdates.length > 0) {
      const filenames = oldUpdates.map(u => u.filename);

      // Delete prices with those filenames
      const { error: pricesDeleteError } = await supabase
        .from('prices')
        .delete()
        .in('filename', filenames);

      if (pricesDeleteError) {
        console.error('Error deleting old prices:', pricesDeleteError.message);
      } else {
        console.log(`Deleted prices for filenames: ${filenames.join(', ')}`);
      }

      // Delete updates themselves
      const { error: updatesDeleteError } = await supabase
        .from('updates')
        .delete()
        .in('filename', filenames);

      if (updatesDeleteError) {
        console.error('Error deleting old updates:', updatesDeleteError.message);
      } else {
        console.log(`Deleted old updates: ${filenames.join(', ')}`);
      }
    } else {
      console.log('No updates older than one week to delete.');
    }
  } catch (err) {
    console.error('Exception during cleanup:', err);
  }

  console.log("--- Update Finished Successfully ---");
}

runUpdate().catch(err => {
  console.error("FAILED:", err);
  Deno.exit(1);
});
