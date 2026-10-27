# Using the assistant: a guide

This guide is for everyday use. You don't need to know how the system is built. If a word is
new to you, it is explained the first time it appears, and again in the glossary at the end.

## 1. What this is

This is a private AI assistant, similar to ChatGPT, that runs on the university's own computers
instead of a company's. You talk to it in your web browser, and it can search the web and
scientific databases, read your papers and figures, and write code. Your chats, papers and
questions stay on your Mac and the university's computers; nothing is sent to an AI company.

## 2. Start and stop

The assistant has two parts. The "brain" (the AI models) runs on a powerful shared computer at
the university with a GPU, a chip built for AI. The chat page runs on your Mac. You start and
stop both with one short command typed into **Terminal**, the Mac app for typing commands.

**To open Terminal:** press ⌘ (Command) and the space bar together, type `Terminal`, and press
Return. A window with a blinking cursor opens. To run a command from this guide, copy it from its
grey box, paste it into that window, and press Return.

### Start

1. Paste this into Terminal and press Return:

   ```
   llm up
   ```

   What you'll see: a few lines that update as it goes, for example `submitted job 4835`, then
   `job 4835: pending in the queue`, then `job 4835: starting on ...`. A **job** is your booking of the
   shared GPU computer.

2. Wait. When the GPU computer is free, starting takes about 3 minutes. When other people are
   using it, your job waits in a **queue** (a waiting line) first; `llm up` waits up to 30
   minutes.

   What you'll see when it's ready:

   ```
   job 4835: ready on ... (took 2:59)
   Ready: http://localhost:3000
   ```

3. Your browser opens the chat page by itself. If it doesn't, open your browser and go to this
   address:

   ```
   http://localhost:3000
   ```

   What you'll see: the chat page, with a message box at the bottom and your past chats on the
   left.

Running `llm up` again while everything is already running is safe: it says so and changes
nothing.

### Check that everything works

1. Paste this into Terminal and press Return:

   ```
   llm status
   ```

   What you'll see (the numbers will differ):

   ```
   job:        4835 running on ..., 11:56:52 left, idle shutdown after 120 min
   tunnel:     up (pid 89270), 127.0.0.1:4000
   containers: arxiv running, docling running, ... open-webui running, ...
   docker:     24.7 GB (...); disk image on the Mac 24.9 GB
   openalex:   9462 of 10000 credits left today, ...
   gateway:    answers (qwen-main, qwen-fast, embed, rerank)
   ```

   The line that matters most is the last one: `gateway: answers` means the assistant can
   answer. `job: none` means nothing is running; start it with `llm up`.

### Stop

1. When you're done for the day, paste this into Terminal and press Return:

   ```
   llm down
   ```

   What you'll see: `cancelled job 4835` and `containers stopped (volumes kept)`. Your chats and
   papers are kept.

**Why stop it?** The GPU computer is shared with colleagues. While your job runs, it holds the GPU
even if you aren't asking anything, so others have to wait. If you forget, the job stops itself
after **2 hours without a question**, and after 12 hours at the most. Your chats stay either way;
`llm up` brings the assistant back.

## 3. Which assistant to pick

At the top of each new chat you pick an assistant. They are the same AI with different tools and
rules, set up for different jobs. Pick the one with the fewest tools that can do the task.

| Assistant | What it's for | Example question | What it can't do |
|---|---|---|---|
| **Research** | Finding and reading papers and web pages, with sources | "What are recent papers on registering micro-CT to histology?" | Create or change files, run code, or see your Private papers |
| **Research (no thinking)** | The same as Research, but faster, because it doesn't think first (see section 4) | "Find the DOI of the paper 'Rolling-Unrolling LSTMs'" | The same as Research; it is less careful on hard questions |
| **Code** | Writing and running code in its own sandbox folder (one folder on your Mac set aside for it) | "Write a Python script that plots column B of data.csv" | Use the internet, or see anything outside its folder |
| **Quick** | Fast, short answers and rewording | "Make this sentence more formal: ..." | Read images, files or the web; it is less careful on hard questions |
| **Figures** | Describing and checking figures and images | "Check this plot for missing labels" (with the image attached) | Search the web or papers |
| **Private** | Questions about your unpublished material | "Summarise the methods section of my draft" | Use the internet; it starts empty until you add your files |

## 4. Everyday tasks

### Ask a question

1. Open http://localhost:3000 in your browser. What you'll see: the chat page.
2. Click **New Chat** at the top left. What you'll see: an empty chat.
3. Click the assistant name at the top of the chat and choose one, for example **Research**.
   What you'll see: its name at the top.
4. Type your question in the box at the bottom and press Return. What you'll see: the answer
   appears word by word. Research can take 1 to 3 minutes, because it searches first.

To ask a follow-up, type it in the same chat; the assistant remembers the conversation. For a
new topic, start a new chat.

### Thinking on and off

"Thinking" means the assistant works through the problem before it answers. This gives better
answers to hard questions, but takes longer.

1. Research, Code, Figures and Private think first. What you'll see: a grey line such as
   **Thought for 12 seconds** above the answer.
2. To read its reasoning, click that grey line. What you'll see: the steps it took. Click again
   to fold it away.
3. To turn thinking off, start a new chat and choose **Research (no thinking)** instead of
   **Research**. It has the same tools and papers, and answers faster. What you'll see: the
   answer without the "Thought for" line.
4. To turn thinking back on, start a new chat with **Research**.

Use Research (no thinking) for simple lookups, such as finding one paper's DOI, and Research
for questions that need judgement, such as which papers matter most. Quick doesn't think
either, but it has no tools.

### Ask about an image or figure

1. Start a new chat and choose **Figures** (Research can read images too).
2. Drag the image file (PNG or JPG) from Finder into the message box, or click **+** next to the
   box and choose **Upload Files**. What you'll see: a small preview of the image above the box.
3. Type your question, for example "What does panel B show?", and press Return. What you'll
   see: a description, then the answer.

Quick can't read images.

### Search the web with sources

1. Start a new chat and choose **Research**. Web search is already on: the **Web Search** button
   under the message box is highlighted.
2. Ask your question and press Return. What you'll see: short status lines such as "Searching
   the web", then the answer.
3. Below the answer, click **Sources** (or a numbered reference in the text) to see which pages
   it used. Open them to check anything important.

### Find papers

1. Start a new chat and choose **Research**.
2. Ask for papers and say what you need, for example: "Find 5 papers since 2023 on deep-learning
   virtual staining from micro-CT, with a one-line summary and a DOI each." What you'll see: the
   assistant searches PubMed, arXiv, OpenAlex and Semantic Scholar (databases of scientific
   papers) and your knowledge bases, then lists the papers.
3. Click each paper's link or **DOI** (a permanent ID for a paper) to check it before you use
   it. The assistant is told never to invent references and to say when it is unsure, but it can
   still describe a paper inaccurately.
4. Treat each summary as a pointer, not as the paper: it is written from the first sentences
   of the abstract. Before you rely on a detail, such as a number, a dataset or a method, open
   the link and check the abstract.

Tip: if you know the exact title of one paper, ask for that title; it finds it faster.

### Ask about your own papers

Your PDFs live in **knowledge bases**: collections of documents the assistant can search. Research
can see four topic collections (Mg implant degradation, CT-histology registration, virtual
staining, video understanding). Private sees only the **Private** collection.

To add a PDF:

1. In the left sidebar, click **Workspace**. What you'll see: tabs along the top.
2. Click **Knowledge**. What you'll see: the list of collections.
3. Click the collection you want, for example **Private**.
4. Click **+** (**Add Content**) and choose **Upload files**.
5. Pick the PDF and click **Open**. What you'll see: the file in the list with a progress mark.
   Reading a paper takes 10 to 50 seconds.
6. When the mark is gone, start a new chat with Research (or Private for the Private collection)
   and ask about it. What you'll see: an answer that names the file it used.

Put unpublished work only in **Private** (see section 5).

### The saved prompts

Saved prompts are ready-made instructions for common jobs, so you don't have to type them each
time.

1. In the message box, type `/`. What you'll see: a list of saved prompts.
2. Click one, for example **/litnote**. What you'll see: its instructions fill the message box.
3. Attach the files it needs (drag them into the box). If the text has a line in square brackets,
   replace that line with your own words.
4. Press Return.

| Prompt | What it does | What to attach | Best assistant |
|---|---|---|---|
| `/litnote` | A structured note on one paper, with page references | one paper (PDF) | Research |
| `/review` | A referee-style review of a draft | your draft (PDF) | Private, if it's unpublished |
| `/figure` | Reads a figure and checks labels, units and scale bars | one image | Figures |
| `/compare` | A side-by-side table of 2 to 5 papers | 2 to 5 papers (PDF) | Research |
| `/related-work` | A related-work section built only from the attached papers, every sentence with its source, plus a list of claims that still need a citation | the papers; replace the bracket line with one line on your work | Research |
| `/commit` | A short description of changes to code, for saving them in git | name the project folder, or paste the changes | Code |

### Where the weekly digest appears

Every **Monday at 10:00** the assistant collects new papers from the past week on your topics
(biomedical and computer vision), with short summaries and links.

1. In the left sidebar, open the folder **Digest**. What you'll see: a chat for each week, named
   after the date.
2. Click the newest one to read it.

The digest only runs if, at 10:00 on Monday, the assistant has been started with `llm up` and
the Mac is awake (not asleep, lid open or plugged in). If it was missed, see section 7.

## 5. Safety habits

- **Unpublished material only in Private.** Drafts, unpublished results and reviews you were
  asked to keep confidential go into the Private collection and Private chats, which can't reach
  the internet. Research reads web pages, and a web page can contain hidden instructions that try
  to make the assistant send your text somewhere.
- **Never put patient data into Research.** Anything that could identify a patient stays out of
  any assistant that uses the web. If in doubt, leave it out.
- **Never paste passwords, keys or tokens into any chat.** A **token** or key is a long secret
  string that works like a password for a service. Chats are stored, and backed up.

## 6. Housekeeping

### Back up your chats and papers

1. Paste this into Terminal and press Return:

   ```
   llm backup
   ```

   What you'll see: `backup: .../open-webui-<date>.tar.gz (95M; Open WebUI paused 1 s)`. The chat
   page pauses for about a second. The last 8 backups are kept.

Do this once a week, and before any big change.

### Free up disk space

The system keeps its programs in **Docker**, a Mac app that runs them in sealed boxes. Old
versions pile up over time.

1. Paste this into Terminal and press Return:

   ```
   llm clean
   ```

   What you'll see: a list of things it could delete and how much space that would free, or
   `nothing to clean`. Nothing is deleted yet.

2. If the list looks fine, paste this and press Return:

   ```
   llm clean --yes
   ```

   What you'll see: the same list under `removing:`, then how much space came back on the Mac.
   Your chats, papers and settings are never on this list.

To see how much space Docker uses, look at the `docker:` line of `llm status`. To see your Mac's
free space: Apple menu → System Settings → General → Storage. Keep at least 20 GB free.

## 7. When something goes wrong

| What you see | What to do |
|---|---|
| The chat shows an error, or the answer never comes | Run `llm status`. If it says `job: none`, the job stopped (2 hours without questions, or the 12-hour limit): run `llm up`. If the job is running but `gateway: no answer`, wait a minute and check again; if it stays, run `llm down`, then `llm up`. |
| The browser says it can't connect to localhost:3000 | The system isn't running. Run `llm up`. |
| `llm up` keeps saying `pending in the queue` | The GPU computer is busy with other people's work. Leave it waiting (up to 30 minutes). If you give up, run `llm down` so your booking doesn't start later when nobody is there. |
| `llm up` stops with `still pending after 30:00` | Your booking is still in the queue. Run `llm up` again to keep waiting, or `llm down` to cancel it. |
| `llm up` stops with `not ready after 15:00` | Run `llm down`, then `llm up`. If it happens again, tell the person who maintains the system, with the job number it showed. |
| "Semantic Scholar unavailable, try OpenAlex" in an answer | Nothing to do. That paper database often refuses requests; the assistant switches to OpenAlex by itself. |
| "OpenAlex daily budget used up, resets at midnight UTC" | The free daily allowance for that paper database is used up. It comes back at 01:00 Berlin time in winter and 02:00 in summer. Until then, Research still uses PubMed, arXiv and Semantic Scholar. `llm status` shows what's left. |
| `Docker is not running`, or `Docker did not start within 2 minutes` | `llm up` normally starts Docker by itself. If not: open **Docker** from the Applications folder, wait until its whale icon in the menu bar stops moving, then run `llm up` again. |
| No new digest on Monday (the Mac was asleep or the assistant was off) | Run `llm up`. Then open http://localhost:3000/automations, click **Weekly literature digest** and choose **Run now**. It takes about 15 minutes and appears in the Digest folder. |

## 8. Glossary

- **Assistant (preset):** a ready-made setup of the AI with its own tools and rules: Research,
  Research (no thinking), Code, Quick, Figures or Private.
- **Cluster:** the university's group of shared, powerful computers.
- **Credits:** the units OpenAlex counts its free daily allowance in.
- **Digest:** the weekly summary of new papers, filed in the Digest folder.
- **DOI:** a permanent ID for a paper, like `10.1038/s41598-023-45518-0`. Its link always
  leads to the paper.
- **Docker:** a Mac app that runs the system's programs, each in its own sealed box (a
  "container").
- **GPU:** a chip built for AI. The AI models need one, so they run on the cluster, not your Mac.
- **GPU job (job):** your booking of a GPU computer on the cluster. `llm up` makes one and `llm down`
  ends it.
- **Idle stop:** the job ends by itself after 2 hours without a question.
- **Knowledge base (collection):** a set of your PDFs the assistant can search and quote.
- **Model:** the AI itself. Several run together: a main one, a fast one (behind Quick), and two
  small helpers for searching your papers.
- **OpenAlex, PubMed, arXiv, Semantic Scholar:** public databases of scientific papers that
  Research searches.
- **Prompt:** what you type to the assistant. A **saved prompt** is a ready-made one you insert
  by typing `/`.
- **Queue:** the waiting line for the cluster's GPU computers.
- **Sandbox:** the one folder on your Mac that the Code assistant may read and change.
- **Terminal:** the Mac app for typing commands such as `llm up`.
- **Thinking:** the assistant working through a problem before answering; shown as "Thought for
  … seconds". Research (no thinking) and Quick skip it.
- **Token:** a long secret string that works like a password for a service. Never paste one
  into a chat.
- **Tunnel:** the private, encrypted connection between your Mac and the cluster. `llm up`
  opens it.
