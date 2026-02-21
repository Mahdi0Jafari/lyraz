// modules/ui.js
import { state } from './state.js';

// کش کردن المنت‌ها
export const elements = {
    title: document.getElementById('track-title'),
    artist: document.getElementById('track-artist'),
    cover: document.getElementById('cover-art'),
    slider: document.getElementById('seek-slider'),
    progressFill: document.getElementById('progress-fill'),
    thumb: document.getElementById('thumb'),
    currTime: document.getElementById('curr-time'),
    totalTime: document.getElementById('total-time'),
    playlistContainer: document.getElementById('playlist-container'),
    mainPlayer: document.getElementById('main-player'),
    loginOverlay: document.getElementById('login-overlay'),
    qrContainer: document.getElementById('qrcode'),
    adminBadge: document.getElementById('admin-badge'),
    adminName: document.getElementById('admin-name'),
    cornerQr: document.getElementById('corner-qr'),
    miniQr: document.getElementById('mini-qrcode'),
    trackCount: document.getElementById('track-count'),
    iconPlay: document.getElementById('icon-play'),
    iconPause: document.getElementById('icon-pause'),
    btnShuffle: document.getElementById('btn-shuffle'),
    btnRepeat: document.getElementById('btn-repeat')
};

// ==========================================
// 🎨 Dynamic Palette Extraction (Zero-Dependency)
// ==========================================
function applyDynamicTheme(imgEl) {
    try {
        const canvas = document.createElement('canvas');
        // willReadFrequently به مرورگر می‌گوید این بوم برای خواندن پیکسل است (بهینه‌سازی پرفورمنس)
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        
        // کوچک کردن بوم برای پردازش فوق‌سریع (کاهش بار CPU روی تلویزیون‌ها)
        canvas.width = 64; 
        canvas.height = 64;
        
        ctx.drawImage(imgEl, 0, 0, canvas.width, canvas.height);
        
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const data = imageData.data;
        
        let r = 0, g = 0, b = 0, count = 0;
        const step = 4 * 5; // پرش از روی پیکسل‌ها برای سرعت بیشتر (هر 5 پیکسل یکی)
        
        for (let i = 0; i < data.length; i += step) {
            // نادیده گرفتن پیکسل‌های خیلی تاریک (سیاه) یا خیلی روشن (سفید) برای رسیدن به رنگ‌های غنی‌تر
            if (data[i] > 15 && data[i] < 240 && data[i+3] > 0) {
                r += data[i];
                g += data[i+1];
                b += data[i+2];
                count++;
            }
        }
        
        if (count > 0) {
            r = Math.floor(r / count);
            g = Math.floor(g / count);
            b = Math.floor(b / count);
        } else {
            // رنگ پیش‌فرض در صورت شکست (سبز اسپاتیفای)
            r = 29; g = 185; b = 84; 
        }

        // تزریق رنگ به متغیر CSS در سطح Root برای استفاده در کل برنامه
        document.documentElement.style.setProperty('--dynamic-rgb', `${r}, ${g}, ${b}`);
    } catch (e) {
        console.warn("⚠️ Canvas Color Extraction Blocked (CORS or Render error)", e);
    }
}

// ==========================================
// 🖥️ توابع آپدیت ظاهر
// ==========================================

export function updatePlayerInfo(track) {
    elements.title.innerText = track.title;
    elements.artist.innerText = track.performer;
    
    elements.cover.style.opacity = 0;
    
    // ⚠️ مهم: برای جلوگیری از خطای امنیتی CORS هنگام خواندن پیکسل‌های تصویر
    elements.cover.crossOrigin = "Anonymous"; 
    
    setTimeout(() => {
        elements.cover.src = `/cover/${track.file_unique_id}`;
        elements.cover.onload = () => { 
            elements.cover.style.opacity = 1; 
            // اعمال تم رنگی بلافاصله پس از لود شدن کاور
            applyDynamicTheme(elements.cover);
        };
    }, 200);
}

export function updatePlayBtn(isPlaying) {
    if(elements.iconPlay) isPlaying ? elements.iconPlay.classList.add('hidden') : elements.iconPlay.classList.remove('hidden');
    if(elements.iconPause) isPlaying ? elements.iconPause.classList.remove('hidden') : elements.iconPause.classList.add('hidden');
}

export function updateProgress(currentTime, duration) {
    if (state.isDragging) return;
    
    if (duration > 0) {
        const pct = (currentTime / duration) * 100;
        if(elements.slider) elements.slider.value = pct;
        if(elements.progressFill) elements.progressFill.style.width = `${pct}%`;
        if(elements.thumb) elements.thumb.style.left = `${pct}%`;
        elements.currTime.innerText = formatTime(currentTime);
        elements.totalTime.innerText = formatTime(duration);
    }
}

export function renderPlaylist(tracks, loadTrackCallback) {
    elements.playlistContainer.innerHTML = "";
    tracks.forEach((track, i) => {
        const div = document.createElement('div');
        div.className = `track-item flex items-center gap-4 p-3 rounded-xl cursor-pointer hover:bg-white/5 transition border border-transparent group`;
        div.onclick = () => loadTrackCallback(i);
        div.innerHTML = `
            <div class="relative w-12 h-12 flex-shrink-0">
                <img src="/cover/${track.file_unique_id}" class="w-full h-full rounded-lg object-cover bg-gray-800" loading="lazy" crossOrigin="Anonymous">
                <div class="absolute inset-0 bg-black/50 rounded-lg flex items-center justify-center opacity-0 group-hover:opacity-100 transition"><i class="ri-play-fill text-white"></i></div>
            </div>
            <div class="flex-1 min-w-0">
                <h4 class="text-sm font-semibold truncate text-gray-200 group-hover:text-white">${track.title}</h4>
                <p class="text-xs text-gray-500 truncate">${track.performer}</p>
            </div>
            <span class="text-xs text-gray-600 font-mono">${formatTime(track.duration)}</span>
        `;
        elements.playlistContainer.appendChild(div);
    });
    updateActiveItem(state.currentIndex);
    if(elements.trackCount) elements.trackCount.innerText = tracks.length;
}

export function updateActiveItem(index) {
    document.querySelectorAll('.track-item').forEach(el => el.classList.remove('active', 'bg-white/10', 'border-white/10'));
    const items = elements.playlistContainer.children;
    if (items[index]) {
        items[index].classList.add('active', 'bg-white/10', 'border-white/10');
        items[index].scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

export function updateControlButtons() {
    // Shuffle UI
    if (elements.btnShuffle) {
        if (state.shuffle) {
            elements.btnShuffle.classList.add('text-primary', 'relative');
            elements.btnShuffle.classList.remove('text-gray-600');
        } else {
            elements.btnShuffle.classList.remove('text-primary', 'relative');
            elements.btnShuffle.classList.add('text-gray-600');
        }
    }
    // Repeat UI
    if (elements.btnRepeat) {
        const iconNormal = elements.btnRepeat.querySelector('.ri-repeat-2-line');
        const iconOne = elements.btnRepeat.querySelector('.ri-repeat-one-line');
        
        elements.btnRepeat.classList.remove('text-primary', 'text-gray-600');
        if (state.repeatMode === 'off') {
            elements.btnRepeat.classList.add('text-gray-600');
            iconNormal?.classList.remove('hidden');
            iconOne?.classList.add('hidden');
        } else {
            elements.btnRepeat.classList.add('text-primary');
            if (state.repeatMode === 'one') {
                iconNormal?.classList.add('hidden');
                iconOne?.classList.remove('hidden');
            } else {
                iconNormal?.classList.remove('hidden');
                iconOne?.classList.add('hidden');
            }
        }
    }
}

export function showLoginQR(url) {
    if(elements.qrContainer && typeof QRCode !== 'undefined') {
        elements.qrContainer.innerHTML = "";
        new QRCode(elements.qrContainer, { text: url, width: 256, height: 256, colorDark: "#000", colorLight: "#fff", correctLevel: QRCode.CorrectLevel.L });
    }
}

export function unlockInterface(admin) {
    elements.loginOverlay.style.opacity = '0';
    setTimeout(() => { elements.loginOverlay.style.display = 'none'; }, 700);
    elements.mainPlayer.classList.remove('opacity-0', 'scale-95');
    
    if(elements.adminName) elements.adminName.innerText = admin.device_display_name || admin.name;
    if(elements.adminBadge) elements.adminBadge.classList.remove('hidden');
    
    if (elements.cornerQr) {
        elements.cornerQr.classList.remove('hidden');
        setTimeout(() => { elements.cornerQr.classList.remove('opacity-0', 'translate-y-10'); }, 1000);
        if(elements.miniQr && typeof QRCode !== 'undefined') {
            elements.miniQr.innerHTML = "";
            new QRCode(elements.miniQr, { text: `${window.location.origin}/connect/${state.sessionToken}`, width: 100, height: 100, colorDark: "#000", colorLight: "#fff", correctLevel: QRCode.CorrectLevel.L });
        }
    }
}

function formatTime(s) { 
    if(!s || isNaN(s)) return "0:00"; 
    const m = Math.floor(s / 60); 
    const sc = Math.floor(s % 60); 
    return `${m}:${sc < 10 ? '0' : ''}${sc}`; 
}