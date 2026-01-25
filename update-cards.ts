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
    const isJace = card.name === "Jace, the Mind Sculptor";

    // Basic Filters
    if (card.legalities?.vintage === 'not_legal' || !card.oracle_id) {
      if (isJace) console.log(`[JACE DEBUG] Skipped: Not Vintage Legal or missing Oracle ID.`);
      continue;
    }

    // Skip specific sets
    if (card.set_name === "Summer Magic / Edgar" || card.set_type === "memorabilia") {
      if (isJace) console.log(`[JACE DEBUG] Skipped: Set "${card.set_name}" is excluded.`);
      continue; 
    }

    // Price Calculation
    const prices = [card.prices?.usd, card.prices?.usd_foil, card.prices?.usd_etched]
      .filter((p): p is string => !!p)
      .map(p => parseFloat(p))
      .filter(price => price > 0.01);

    if (prices.length === 0) {
      if (isJace) console.log(`[JACE DEBUG] Skipped: No prices > 0.01 in set "${card.set_name}".`);
      continue;
    }
    
    const minPrice = Math.min(...prices);

    // Logic: Determine if this printing is the new cheapest
    const alreadyInDict = card.oracle_id in cardDict;
    const isCheaper = !alreadyInDict || minPrice < cardDict[card.oracle_id].price;

    if (isCheaper) {
      if (isJace) {
        console.log(`[JACE DEBUG] NEW CHEAPEST: ${card.set_name} | Price: $${minPrice} | Previous: $${alreadyInDict ? cardDict[card.oracle_id].price : 'None'}`);
      }

      let adjustedRank = card.edhrec_rank;
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
    } else if (isJace) {
      console.log(`[JACE DEBUG] Ignored: ${card.set_name} ($${minPrice}) is more expensive than current ($${cardDict[card.oracle_id].price}).`);
    }
  }

  // Double check if Jace made it into the final dictionary
  const jaceOracleId = "ae430263-9566-4074-90f7-53531633cf48"; // Standard Jace Oracle ID
  if (!cardDict[jaceOracleId]) {
    console.warn("!!! [WARNING] Jace, the Mind Sculptor was NOT found in the final card dictionary.");
  }

  // 5. Record the update
  try {
    const { error: updatesError } = await supabase.from('updates').insert({ filename: target.updated_at });
    if (updatesError) {
      console.error(`Error recording update: ${updatesError.message}`);
      return; 
    }
  } catch (err) {
    console.error(`Exception recording update: ${err}`);
    return;
  }

  // 6. Bulk Upsert
  const entries = Object.values(cardDict);
  const batchSize = 1000;
  console.log(`Upserting ${entries.length} cards...`);

  for (let i = 0; i < entries.length; i += batchSize) {
    const batch = entries.slice(i, i + batchSize);
    
    try {
      const { error: cardsError } = await supabase.from('cards').upsert(batch.map(c => ({
        oracle_id: c.oracle_id,
        name: c.name,
        edhrec_rank: c.edhrec_rank,
        tcgplayer_id: c.tcgplayer_id,
        is_staple: c.is_staple,
        is_disincentivized: c.is_disincentivized
      })));
      if (cardsError) console.error(`Error upserting cards: ${cardsError.message}`);
    } catch (err) {
      console.error(`Exception upserting cards: ${err}`);
    }

    try {
      const { error: pricesError } = await supabase.from('prices').insert(batch.map(c => ({
        oracle_id: c.oracle_id,
        price: c.price,
        date: c.date,
        filename: target.updated_at
      })));
      if (pricesError) console.error(`Error inserting prices: ${pricesError.message}`);
    } catch (err) {
      console.error(`Exception inserting prices: ${err}`);
    }
  }

  console.log("--- Update Finished Successfully ---");
}

runUpdate().catch(err => {
  console.error("FAILED:", err);
  Deno.exit(1);
});
