document.addEventListener("DOMContentLoaded", function() {
  if (typeof YEAR_MONTH === "undefined") return;
  const listEl = document.getElementById("tweet-list");
  const loadMoreBtn = document.getElementById("load-more");
  const doneMsg = document.getElementById("done-msg");
  if (!listEl) return;

  function formatTweetDate(iso) {
    try {
      const d = new Date(iso);
      const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
      const date = d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
      return time + " \u00b7 " + date;
    } catch (e) { return iso; }
  }

  fetch("../data/" + YEAR_MONTH + ".json")
    .then(function(r) { return r.json(); })
    .then(function(data) {
      const tweets = data.tweets || [];
      let shown = 0;
      const pageSize = typeof PAGE_SIZE !== "undefined" ? PAGE_SIZE : 50;

      function renderTweet(t) {
        const card = document.createElement("a");
        card.className = "tweet-card";
        card.href = "../tweet/" + t.id + ".html";
        const avatar = document.createElement("div");
        avatar.className = "tweet-avatar";
        avatar.textContent = "S";
        card.appendChild(avatar);
        const body = document.createElement("div");
        body.className = "tweet-body";
        const header = document.createElement("div");
        header.className = "tweet-header";
        const name = document.createElement("span");
        name.className = "tweet-name";
        name.textContent = "State Dept";
        const handle = document.createElement("span");
        handle.className = "tweet-handle";
        handle.textContent = "@StateDept";
        const dot = document.createElement("span");
        dot.className = "tweet-dot";
        dot.textContent = " \u00b7 ";
        const meta = document.createElement("span");
        meta.className = "tweet-meta";
        meta.textContent = formatTweetDate(t.created_at);
        header.appendChild(name);
        header.appendChild(handle);
        header.appendChild(dot);
        header.appendChild(meta);
        body.appendChild(header);
        const text = document.createElement("div");
        text.className = "tweet-text" + (t.text.length > 200 ? " truncated" : "");
        text.textContent = t.text.length > 200 ? t.text.slice(0, 200) + "\u2026" : t.text;
        body.appendChild(text);
        if (t.media_count > 0) {
          const mediaLink = document.createElement("span");
          mediaLink.className = "tweet-link";
          mediaLink.textContent = t.media_count + " photo" + (t.media_count !== 1 ? "s" : "");
          body.appendChild(mediaLink);
        }
        card.appendChild(body);
        return card;
      }

      function showNext() {
        const end = Math.min(shown + pageSize, tweets.length);
        for (let i = shown; i < end; i++) {
          listEl.appendChild(renderTweet(tweets[i]));
        }
        shown = end;
        if (shown >= tweets.length) {
          loadMoreBtn.style.display = "none";
          doneMsg.style.display = "block";
          doneMsg.textContent = "All " + tweets.length + " tweets shown.";
        } else {
          loadMoreBtn.style.display = "block";
          loadMoreBtn.textContent = "Load more (" + (tweets.length - shown) + " left)";
        }
      }

      loadMoreBtn.addEventListener("click", function() { showNext(); });
      showNext();
    })
    .catch(function(e) {
      listEl.innerHTML = "<p>Failed to load month data.</p>";
      console.error(e);
    });
});
