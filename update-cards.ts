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

async function* parseJsonArray(raw: Uint8Array, chunkSize = 65536): AsyncGenerator<Card> {
  const decoder = new TextDecoder();
  let buf = '';
  let depth = 0;
  let inString = false;
  let escaped = false;
  let arrayStarted = false;
  let objectStart = -1;

  for (let offset = 0; offset < raw.length; offset += chunkSize) {
    const end = Math.min(offset + chunkSize, raw.length);
    buf += decoder.decode(raw.slice(offset, end), { stream: true });

    let i = 0;
    while (i < buf.length) {
      const char = buf[i];

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
            yield JSON.parse(buf.slice(objectStart, i + 1));
            objectStart = -1;
          }
        }
      }

      i++;
    }

    if (objectStart >= 0) {
      buf = buf.slice(objectStart);
      objectStart = 0;
    } else {
      buf = '';
    }
  }
}

async function downloadFile(url: string, dest: string, retries = 3): Promise<void> {
  for (let attempt = 1; attempt <= retries; attempt++) {
    let file: Deno.FsFile | undefined;
    try {
      const response = await fetch(url);
      const reader = response.body!.getReader();
      file = await Deno.open(dest, { write: true, create: true });
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        await file.write(value);
      }
      file.close();
      return;
    } catch (err) {
      try { file?.close(); } catch {}
      if (attempt === retries) throw err;
      console.log(`Download failed (attempt ${attempt}), retrying in 5s...`);
      await new Promise(r => setTimeout(r, 5000));
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

  // 3. Download to temp file (fast, bytes-only, no string limit)
  const tmpFile = await Deno.makeTempFile({ suffix: '.json' });
  console.log("Downloading 500MB+ Scryfall data...");
  await downloadFile(target.download_uri, tmpFile);
  console.log("Download complete, parsing...");

  const rawBytes = await Deno.readFile(tmpFile);

  // 4. Transform Data
  const cardDict: Record<string, any> = {};
  const now = new Date().toISOString();
  let processedCount = 0;
  let keptCount = 0;

  for await (const card of parseJsonArray(rawBytes)) {
    processedCount++;
    if (processedCount % 1000 === 0) {
      console.log(`Stream progress: ${processedCount} cards read, ${keptCount} kept so far`);
    }
    // Basic Filters
    if (card.legalities?.vintage === 'not_legal' || !card.oracle_id) {
      continue;
    }

    // Skip specific sets (Memorabilia/Forbidden sets)
    if (card.set_name === "Summer Magic / Edgar" || card.set_type === "memorabilia") {
      continue; 
    }

    // Price Calculation
    const prices = [card.prices?.usd, card.prices?.usd_foil, card.prices?.usd_etched]
      .filter((p): p is string => !!p)
      .map(p => parseFloat(p))
      .filter(price => price > 0.01);

    if (prices.length === 0) {
      continue;
    }
    
    const minPrice = Math.min(...prices);

    // Logic: Keep the absolute cheapest printing found across all sets
    const alreadyInDict = card.oracle_id in cardDict;
    const isCheaper = !alreadyInDict || minPrice < cardDict[card.oracle_id].price;

    if (isCheaper) {

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

      keptCount++;
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
  await Deno.remove(tmpFile);

  console.log(`Stream complete: ${processedCount} cards read, ${keptCount} kept`);

  // Final Verification Check
  const finalEntries = Object.values(cardDict);

  // 5. Record the update metadata
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

  // 6. Bulk Upsert in Batches
  const batchSize = 1000;
  console.log(`Upserting ${finalEntries.length} cards in batches...`);

  for (let i = 0; i < finalEntries.length; i += batchSize) {
    const batch = finalEntries.slice(i, i + batchSize);
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

    if (i % 10000 === 0) console.log(`Progress: ${i} / ${finalEntries.length}`);
  }

  // 7. Cleanup (Only runs if the upsert finished)
  try {
    const oneWeekAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
    const { data: oldUpdates } = await supabase.from('updates').select('filename').lt('filename', oneWeekAgo);

    if (oldUpdates && oldUpdates.length > 0) {
      const filenames = oldUpdates.map(u => u.filename);
      await supabase.from('prices').delete().in('filename', filenames);
      await supabase.from('updates').delete().in('filename', filenames);
      console.log(`Cleaned up ${filenames.length} old entries.`);
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
