import html
import json
import re

def write_html(items, html_path: str, css_path: str):
    def get(it, k, default=""):
        if hasattr(it, k):
            v = getattr(it, k)
        else:
            v = it.get(k, default)
        return "" if v is None else str(v)

    with open(css_path, "r", encoding="utf-8") as f:
        css_text = f.read()

    groups = {}
    for it in items:
        j = get(it, "Journal")
        v = get(it, "Volume")
        groups.setdefault((j, v), []).append(it)

    def vol_num(vol: str) -> int:
        import re
        m = re.search(r"Volume\s+(\d+)", vol or "", flags=re.I)
        return int(m.group(1)) if m else 0

    sorted_keys = sorted(groups.keys(), key=lambda kv: (kv[0], vol_num(kv[1]), kv[1]))

    parts = []
    parts.append("<!doctype html>")
    parts.append('<html lang="zh-CN">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append("<style>")
    parts.append(css_text)
    parts.append("</style>")
    parts.append("</head>")
    parts.append("<body>")

    # 搜索框
    parts.append("""
<div id=\"search\">
  <input id="searchInput" type="text" placeholder="Search title / page / date / volume / journal"/>
  <button onclick="filterList()">Search</button>
  <button onclick="clearWatched()">Clear Watched</button>
</div>
""")

    for (journal, volume) in sorted_keys:
        parts.append("<div>")
        parts.append(f"<span>{html.escape(journal)}</span><span>{html.escape(volume)}</span>")
        parts.append("</div>")
        parts.append('<ul class="paper-list">')

        def sort_key(it):
            date_str = get(it, "Date")
            try:
                date_val = int(date_str)
            except:
                date_val = 0
            return (-date_val, get(it, "Page"), get(it, "Title"))

        for it in sorted(groups[(journal, volume)], key=sort_key):
            title = html.escape(get(it, "Title"))
            link = get(it, "Link")
            page = html.escape(get(it, "Page"))
            date = html.escape(get(it, "Date"))

            data_all = html.escape(
                f"{journal} {volume} {get(it,'Title')} {get(it,'Page')} {get(it,'Date')}",
                quote=True
            )

            if link:
                link_esc = html.escape(link, quote=True)
                parts.append(
                    f'<li data-all="{data_all}">'
                    f'<a href="{link_esc}" target="_blank" rel="noopener noreferrer">{title}</a> '
                    f'<tag>{page}</tag> <tag>{date}</tag>'
                    f'</li>'
                )
            else:
                parts.append(
                    f'<li data-all="{data_all}">'
                    f'<tag>Loss Link</tag> <p>{title}</p> <tag>{page}</tag> <tag>{date}</tag>'
                    f'</li>'
                )

        parts.append("</ul>")

    # JS 过滤逻辑
    parts.append("""
<script>
function filterList() {
  const input = document.getElementById("searchInput");
  const keyword = input.value.toLowerCase().trim();
  const items = document.querySelectorAll("li[data-all]");

  items.forEach(li => {
    const text = li.getAttribute("data-all").toLowerCase();
    if (keyword === "" || text.includes(keyword)) {
      li.style.display = "";
    } else {
      li.style.display = "none";
    }
  });
}

document.getElementById("searchInput").addEventListener("keyup", function(e) {
  if (e.key === "Enter") {
    filterList();
  }
});
document.addEventListener("DOMContentLoaded", function () {

  const links = document.querySelectorAll("li a");

  links.forEach(link => {
    const key = "watched_" + link.href;

    // 页面加载时恢复状态
    if (localStorage.getItem(key)) {
      markWatched(link);
    }

    link.addEventListener("click", function () {
      localStorage.setItem(key, "1");
      markWatched(link);
    });
  });

  function markWatched(link) {
    if (link.parentElement.querySelector("pin")) return;

    const tag = document.createElement("pin");
    tag.textContent = "watched";

    link.parentElement.insertBefore(tag, link);
  }

});
function clearWatched() {
  for (let i = localStorage.length - 1; i >= 0; i--) {
    const key = localStorage.key(i);
    if (key && key.startsWith("watched_")) {
      localStorage.removeItem(key);
    }
  }
  document.querySelectorAll("pin").forEach(p => p.remove());
}
</script>
""")

    parts.append("</body></html>")

    with open(html_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


if __name__ == "__main__":
    from main import read_config
    cfg = read_config("Config.ini")

    m = re.search(r"(\d{4})-(\d{4})", cfg["output_json"])
    if m:
        year1, year2 = m.groups()
    with open(cfg["output_json"], "r", encoding="utf-8") as f:
        data = json.load(f)
        write_html(data, f"index-{year1}-{year2}.html", "styles.css")