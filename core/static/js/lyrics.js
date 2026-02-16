/**
 * Lyrics Manager Module (TV Mode Optimized)
 * Features: Synced Scrolling, Click-to-Seek
 */

let lyricsData = [];
// 🔥 پیش‌فرض true است چون لیریک همیشه در دیزاین جدید نمایش داده می‌شود
let isLyricsViewActive = true; 

const lyricsPanel = document.getElementById('lyrics-panel');
const lyricsBtn = document.getElementById('lyrics-btn');

// --- Main Functions ---

async function fetchLyrics(uniqueId) {
    if (!lyricsPanel) return;
    
    // پاکسازی و نمایش لودینگ
    lyricsData = [];
    lyricsPanel.innerHTML = '<div class="h-full flex items-center justify-center"><span class="animate-pulse text-primary text-lg font-bold">Searching...</span></div>';
    
    // اگر دکمه لیریک وجود دارد (در موبایل)، آن را فعال نشان بده
    if(lyricsBtn) lyricsBtn.classList.add('text-primary');

    try {
        const res = await fetch(`/stream/lyrics/${uniqueId}`);
        const data = await res.json();

        if (data.status === 'found') {
            parseLRC(data.lyrics);
            renderLyricsUI();
        } else {
            lyricsPanel.innerHTML = '<div class="h-full flex items-center justify-center text-white/30 text-sm">Lyrics not available</div>';
        }
    } catch (e) {
        console.error("Lyrics Error:", e);
        lyricsPanel.innerHTML = '<div class="h-full flex items-center justify-center text-red-400 text-xs">Connection Error</div>';
    }
}

function parseLRC(lrcText) {
    const regex = /^\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)/;
    
    lyricsData = lrcText.split('\n').map(line => {
        const match = line.match(regex);
        if (!match) return null;
        
        const min = parseInt(match[1]);
        const sec = parseInt(match[2]);
        // هندل کردن میلی‌ثانیه ۲ رقمی یا ۳ رقمی برای دقت بالا
        const ms = match[3].length === 3 ? parseInt(match[3]) : parseInt(match[3]) * 10;
        
        const time = min * 60 + sec + (ms / 1000);
        const text = match[4].trim();
        
        return { time, text };
    }).filter(item => item && item.text); 
}

function renderLyricsUI() {
    lyricsPanel.innerHTML = '';
    
    // فضای خالی بالا (برای اینکه خط اول وسط بیاید - حالت Cinematic)
    const spacer = document.createElement('div');
    spacer.style.minHeight = "40%"; 
    lyricsPanel.appendChild(spacer);

    lyricsData.forEach((line, index) => {
        const p = document.createElement('p');
        p.className = 'lyric-line'; // استایل‌ها از فایل style.css خوانده می‌شوند
        p.innerText = line.text;
        p.id = `line-${index}`;
        
        // قابلیت Seek با کلیک روی متن
        // این تابع حالا در player.js تعریف و اکسپورت شده است
        p.onclick = () => {
            if (window.seekToTime) {
                window.seekToTime(line.time);
                
                // افکت ویبره برای فیدبک لمسی (اگر مرورگر ساپورت کند)
                if (navigator.vibrate) navigator.vibrate(20);
            } else {
                console.warn("Seek function not ready yet.");
            }
        };
        
        lyricsPanel.appendChild(p);
    });

    // فضای خالی پایین
    const spacerBottom = document.createElement('div');
    spacerBottom.style.minHeight = "60%";
    lyricsPanel.appendChild(spacerBottom);
}

function syncLyrics(currentTime) {
    // اگر لیریک نداریم یا پنل مخفی است، پردازش نکن (بهینه‌سازی CPU)
    if (!isLyricsViewActive || lyricsData.length === 0 || !lyricsPanel) return;

    // الگوریتم پیدا کردن خط فعلی
    let activeIndex = -1;
    for (let i = 0; i < lyricsData.length; i++) {
        if (lyricsData[i].time <= currentTime) {
            activeIndex = i;
        } else {
            break;
        }
    }

    if (activeIndex !== -1) {
        // حذف کلاس اکتیو از خط قبلی
        const current = lyricsPanel.querySelector('.active');
        
        // بهینه‌سازی: اگر خط فعلی همان خط قبلی است، DOM را دستکاری نکن (جلوگیری از پرش)
        if (current && current.id === `line-${activeIndex}`) return;
        
        if (current) current.classList.remove('active');

        // فعال کردن خط جدید و اسکرول به آن
        const newLine = document.getElementById(`line-${activeIndex}`);
        if (newLine) {
            newLine.classList.add('active');
            
            // اسکرول نرم و سینمایی به وسط صفحه
            newLine.scrollIntoView({ 
                behavior: 'smooth', 
                block: 'center' 
            });
        }
    }
}

function toggleLyrics() {
    isLyricsViewActive = !isLyricsViewActive;
    
    if (isLyricsViewActive) {
        // نمایش پنل با انیمیشن
        lyricsPanel.classList.remove('opacity-0', 'pointer-events-none');
        lyricsPanel.classList.remove('hidden'); 
        if(lyricsBtn) lyricsBtn.classList.add('text-primary'); 
        
        // سینک فوری برای اینکه کاربر معطل نشود
        if(window.audio) syncLyrics(window.audio.currentTime);
    } else {
        // مخفی کردن پنل
        lyricsPanel.classList.add('opacity-0', 'pointer-events-none');
        if(lyricsBtn) lyricsBtn.classList.remove('text-primary'); 
    }
}

// اکسپورت توابع به ویندو (برای دسترسی از player.js)
window.fetchLyrics = fetchLyrics;
window.syncLyrics = syncLyrics;
window.toggleLyrics = toggleLyrics;