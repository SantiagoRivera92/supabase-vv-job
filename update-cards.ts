import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';

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

async function* streamScryfallCards(url: string): AsyncGenerator<Card> {
  const response = await fetch(url);
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();

  let buffer = '';
  let depth = 0;
  let inString = false;
  let escaped = false;
  let arrayStarted = false;
  let objectStart = -1;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    let i = 0;
    while (i < buffer.length) {
      const char = buffer[i];

      if (escaped) {
        escaped = false;
        i++;
        continue;
      }

      if (char === '\\' && inString) {
        escaped = true;
        i++;
        continue;
      }

      if (!arrayStarted) {
        if (char === '[') arrayStarted = true;
        i++;
        continue;
      }

      if (char === '"') {
        inString = !inString;
        i++;
        continue;
      }

      if (!inString) {
        if (char === '{') {
          if (depth === 0) objectStart = i;
          depth++;
        } else if (char === '}') {
          depth--;
          if (depth === 0 && objectStart >= 0) {
            yield JSON.parse(buffer.slice(objectStart, i + 1));
            objectStart = -1;
          }
        }
      }

      i++;
    }

    if (objectStart >= 0) {
      buffer = buffer.slice(objectStart);
      objectStart = 0;
    } else {
      buffer = '';
    }
  }
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

  // 3. Download & Stream-Parse
  console.log("Downloading and stream-parsing 500MB+ Scryfall data...");

  // 4. Transform Data
  const cardDict: Record<string, any> = {};
  const now = new Date().toISOString();

  for await (const card of streamScryfallCards(target.download_uri)) {
    // Basic Filters
    if (card.legalities?.vintage === 'not_legal' || !card.oracle_id) continue;

    if (card.set_name === "Summer Magic / Edgar" || card.set_type === "memorabilia") {
      console.log("Skipping", card.name, "from set ", card.set_name);
      continue; 
    }

    // Price Calculation
    const prices = [card.prices?.usd, card.prices?.usd_foil, card.prices?.usd_etched]
      .filter((p): p is string => !!p)
      .map(p => parseFloat(p));

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

  console.log("--- Update Finished Successfully ---");
}

runUpdate().catch(err => {
  console.error("FAILED:", err);
  Deno.exit(1);
});
