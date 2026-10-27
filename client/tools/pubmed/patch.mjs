// Build-time patch of cyanheads/pubmed-mcp-server 2.10.22 (D-023): search results carry the first
// two sentences of each abstract, or "abstract: not available - list by title and link only".
// - pubmed_search_articles: one batched EFetch for the summarised PMIDs (ESummary has no
//   abstracts), parsed with the server's own article parser.
// - pubmed_europepmc_search: the snippet Europe PMC already returns, cut after two sentences
//   instead of at 400 characters.
// Every replacement must match exactly once, or the build fails: a new upstream version needs
// this file reviewed.
import { readFileSync, writeFileSync } from 'node:fs';

const dir = process.argv[2];

const HELPER = `
// local-llm patch (D-023): the first two sentences, at most about 450 characters, marked with
// " …" when the abstract goes on.
function firstSentences(text) {
    const t = (text ?? '').replace(/\\s+/g, ' ').trim();
    if (!t)
        return '';
    let out = t.split(/(?<=[.!?])\\s+(?=[A-Z0-9(\\[])/).slice(0, 2).join(' ');
    if (out.length > 450)
        out = out.slice(0, 450).replace(/\\s+\\S*$/, '');
    return out.length < t.length ? \`\${out} …\` : out;
}
`;

function patch(file, edits) {
  const path = `${dir}/${file}`;
  let src = readFileSync(path, 'utf8');
  for (const [from, to] of edits) {
    const n = src.split(from).length - 1;
    if (n !== 1) {
      console.error(`${file}: expected 1 match, found ${n}:\n${from}`);
      process.exit(1);
    }
    src = src.replace(from, to);
  }
  writeFileSync(path, src);
  console.log(`patched ${file} (${edits.length} edits)`);
}

patch('search-articles.tool.js', [
  [
    "import { extractBriefSummaries } from '../../../services/ncbi/parsing/esummary-parser.js';\n",
    "import { extractBriefSummaries } from '../../../services/ncbi/parsing/esummary-parser.js';\n" +
      "import { parseArticleSet } from '../../../services/ncbi/parsing/article-parser.js';\n" +
      HELPER,
  ],
  [
    "            pubmedUrl: z.string().optional().describe('PubMed URL'),\n",
    "            pubmedUrl: z.string().optional().describe('PubMed URL'),\n" +
      "            abstract: z.string().optional().describe('First two sentences of the abstract; empty when PubMed has none'),\n",
  ],
  [
    "                    pubmedUrl: `https://pubmed.ncbi.nlm.nih.gov/${s.pmid}/`,\n                }));\n            }\n        }\n",
    "                    pubmedUrl: `https://pubmed.ncbi.nlm.nih.gov/${s.pmid}/`,\n                }));\n            }\n        }\n" +
      "        // local-llm patch (D-023): abstracts for the summarised PMIDs, one batched EFetch.\n" +
      "        if (summaries.length > 0) {\n" +
      "            try {\n" +
      "                const ids = summaries.map((s) => s.pmid);\n" +
      "                const xml = await ncbi.eFetch({ db: 'pubmed', id: ids.join(','), retmode: 'xml' }, { retmode: 'xml', usePost: ids.length >= 100, signal: ctx.signal });\n" +
      "                const byPmid = new Map(parseArticleSet(xml?.PubmedArticleSet).map((a) => [a.pmid, a.abstractText]));\n" +
      "                summaries = summaries.map((s) => ({ ...s, abstract: firstSentences(byPmid.get(s.pmid)) }));\n" +
      "            }\n" +
      "            catch (e) {\n" +
      "                ctx.log.warning('abstract EFetch failed', { error: String(e) });\n" +
      "            }\n" +
      "        }\n",
  ],
  [
    "                if (s.pmcUrl)\n                    lines.push(`**PMC:** ${s.pmcUrl}`);\n",
    "                if (s.pmcUrl)\n                    lines.push(`**PMC:** ${s.pmcUrl}`);\n" +
      "                lines.push(`**abstract:** ${s.abstract || 'not available - list by title and link only'}`);\n",
  ],
]);

patch('pubmed-europepmc-search.tool.js', [
  ['const ABSTRACT_SNIPPET_LIMIT = 400;\n', 'const ABSTRACT_SNIPPET_LIMIT = 400;\n' + HELPER],
  [
    '            const truncated = abstract.length > ABSTRACT_SNIPPET_LIMIT;\n',
    '            const snippet = firstSentences(abstract);\n            const truncated = snippet.endsWith(\' …\');\n',
  ],
  [
    '                    abstractSnippet: truncated\n                        ? `${sliceCodeUnits(abstract, ABSTRACT_SNIPPET_LIMIT)}…`\n                        : abstract,\n',
    '                    abstractSnippet: snippet,\n',
  ],
  [
    '                if (h.abstractSnippet) {\n                    lines.push(`\\n${h.abstractSnippet}`);\n                    if (h.abstractTruncated) {\n                        lines.push(`_Abstract truncated at ${ABSTRACT_SNIPPET_LIMIT} characters — pass this hit to \\`pubmed_europepmc_fetch\\` as \\`records: [{ source, epmcId }]\\` for the complete text._`);\n                    }\n                }\n',
    '                lines.push(`**abstract:** ${h.abstractSnippet || \'not available - list by title and link only\'}`);\n                if (h.abstractTruncated) {\n                    lines.push(`_First sentences only — pass this hit to \\`pubmed_europepmc_fetch\\` as \\`records: [{ source, epmcId }]\\` for the complete abstract._`);\n                }\n',
  ],
]);
