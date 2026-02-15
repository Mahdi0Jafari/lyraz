// modules/state.js

export const CONFIG = {
    retryLimit: 3,
    preloadGap: 15
};

export const state = {
    tracks: [],
    currentIndex: 0,
    isPlaying: false,
    isDragging: false,
    sessionToken: localStorage.getItem('fanus_session_token'),
    retryCount: 0,
    
    // تنظیمات پخش
    shuffle: false,
    repeatMode: 'all', // 'off', 'all', 'one'
    
    // وضعیت ریکاوری نت
    recovery: {
        active: false,
        time: 0,
        trackId: null,
        wasPlaying: false
    }
};