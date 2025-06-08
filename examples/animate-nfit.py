#!/usr/bin/env python3
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.widgets import Button # Import the Button widget
import pandas as pd
import re # Import regular expressions for parsing profile names

# Apply a professional plot style
plt.style.use("seaborn-v0_8-darkgrid")

# --- 0. Synthetic Data Generation ---
np.random.seed(42)
num_days = 90
points_per_day = 24
total_points = num_days * points_per_day
time_days = np.linspace(0, num_days, total_points)
baseline = 3 + np.sin(2 * np.pi * time_days / 7) * 1.0
noise = np.random.normal(0, 0.4, total_points)
spikes = np.zeros(total_points)
for _ in range(15):
    spike_idx = np.random.randint(0, total_points)
    spike_duration = np.random.randint(1, 4)
    spike_val = np.random.uniform(4, 7)
    for i in range(spike_duration):
        if spike_idx + i < total_points:
            spikes[spike_idx + i] = spike_val * (1 - i * 0.3)
growth_trend = 0.025 * time_days
raw_cpu_data = baseline + noise + spikes + growth_trend
raw_cpu_data = np.clip(raw_cpu_data, 0.2, None)
s_raw_cpu = pd.Series(raw_cpu_data)

# --- EMA Calculations for Different Conceptual Profiles ---
# User-defined alpha values for the primary EMA of each profile
USER_DEFINED_ALPHAS = {
    "P-99W1 (Peak, extreme)": 0.50,
    "O1-99W5 (Tier 1, very-high)": 0.3,
    "O2-98W10 (Tier 2, high)": 0.15,
#    "O3-95W15 (Tier 3, responsive)": 0.8,
    "O4-90W15 (Tier 4, normal)": 0.01
}

active_profile_names_ordered = [
    "P-99W1 (Peak, extreme)",
    "O1-99W5 (Tier 1, very-high)",
    "O2-98W10 (Tier 2, high)",
    "O4-90W15 (Tier 4, normal)"
]

profile_emas_data = {}
for name in active_profile_names_ordered:
    if name in USER_DEFINED_ALPHAS:
        alpha = USER_DEFINED_ALPHAS[name]
        profile_emas_data[name] = s_raw_cpu.ewm(alpha=alpha, adjust=False, ignore_na=True).mean()
    else:
        profile_emas_data[name] = pd.Series(dtype=float) 

profile_colors = ['limegreen', 'mediumorchid', 'orangered', 'darkorange', 'khaki']
active_profile_emas = {name: data for name, data in profile_emas_data.items() if data is not None and not data.empty}
num_active_profiles = len(active_profile_emas)

# --- Helper functions to extract percentile and W-value from profile name ---
def get_percentile_from_name(profile_name):
    match = re.search(r'-(\d+)W', profile_name)
    if match: return int(match.group(1))
    match = re.search(r'P(\d+)', profile_name)
    if match:
        val = int(match.group(1))
        if 0 <= val <= 100: return val
    numbers = re.findall(r'\d+', profile_name)
    for num_str in reversed(numbers):
        num = int(num_str)
        if 70 <= num <= 100: return num
    return 90

def get_w_value_from_name(profile_name):
    match = re.search(r'W(\d+)', profile_name)
    if match:
        val = int(match.group(1))
        return max(1, val)
    return 1

# --- Animation Speed, Phase Durations, and Delay Control Parameters ---
animation_interval_ms = 50
frames_raw_data = 280
if num_active_profiles > 0 :
    frames_per_profile_ema_target = 200
    frames_total_ema_drawing = frames_per_profile_ema_target * num_active_profiles
    frames_per_profile_ema = frames_total_ema_drawing // num_active_profiles
else:
    frames_total_ema_drawing = 0
    frames_per_profile_ema = 0
frames_regression_buildup = num_days
frames_delay_after_raw = 100
frames_delay_between_emas = 100
frames_delay_after_all_emas = 120
pause_duration_before_repeat_ms = 5000
frames_for_loop_pause = int(pause_duration_before_repeat_ms / animation_interval_ms) if animation_interval_ms > 0 else 0
if frames_for_loop_pause <= 0 and pause_duration_before_repeat_ms > 0:
    frames_for_loop_pause = 1
total_profile_ema_animation_frames = num_active_profiles * frames_per_profile_ema
total_inter_ema_delay_frames = max(0, num_active_profiles - 1) * frames_delay_between_emas
frames_for_actual_content = (frames_raw_data + frames_delay_after_raw +
                             total_profile_ema_animation_frames + total_inter_ema_delay_frames +
                             frames_delay_after_all_emas + frames_regression_buildup)
total_frames_for_animation = frames_for_actual_content + frames_for_loop_pause

# --- Matplotlib Figure Setup ---
fig, ax = plt.subplots(figsize=(15, 7))
plt.subplots_adjust(bottom=0.2)
line_raw_plot, = ax.plot([], [], lw=0.8, color='deepskyblue', label='Raw CPU Usage', alpha=0.6)
profile_lines_plots = []
for i, name in enumerate(active_profile_names_ordered):
    if name in active_profile_emas:
        color_idx = i % len(profile_colors)
        line, = ax.plot([], [], lw=2.0, label=name, color=profile_colors[color_idx], alpha=0.8)
        profile_lines_plots.append(line)

scatter_reg_points = ax.scatter([], [], color='steelblue', label='Daily P95 of Smoothest EMA', s=20, alpha=0.7, zorder=5)
line_reg_plot, = ax.plot([], [], color='crimson', lw=2.5, label='Linear Regression Trend', zorder=4, linestyle='-.')
ax.set_ylim(0, np.max(raw_cpu_data) * 1.15 if total_points > 0 else 10)
ax.set_xlim(0, num_days)
ax.set_xlabel("Time (Days)")
ax.set_ylabel("CPU Cores")
ax.set_title("Visualising nFit (Niël Lambrechts)") # As per user's v2
ax.legend(loc='upper left', fontsize='small', title='Legend Details',
          frameon=True, facecolor='ivory', edgecolor='black',
          shadow=True, borderpad=0.7)
for i in range(0, num_days + 7, 7):
    if i <= num_days:
        ax.axvline(i, color='dimgray', linestyle='--', lw=1.0, alpha=0.7,
                   label='7-Day Window Split' if i == 0 else "_nolegend_")
daily_p95_values = []
daily_time_points = []
if num_active_profiles > 0:
    last_active_profile_name = None
    for name in reversed(active_profile_names_ordered):
        if name in active_profile_emas:
            last_active_profile_name = name
            break
    if last_active_profile_name:
        smoothest_ema_series_for_reg = active_profile_emas[last_active_profile_name]
        if not smoothest_ema_series_for_reg.empty:
            for day_idx in range(num_days):
                start_idx = day_idx * points_per_day
                end_idx = (day_idx + 1) * points_per_day
                if start_idx < len(smoothest_ema_series_for_reg):
                    day_data = smoothest_ema_series_for_reg[start_idx:min(end_idx, len(smoothest_ema_series_for_reg))]
                    if not day_data.empty:
                        daily_p95_values.append(np.percentile(day_data, 95))
                        daily_time_points.append(day_idx + 0.5)

pxx_text_artist = None
pxx_horizontal_line = None # Global artist for the Pxx horizontal line

# --- Function to display Pxx for a profile ---
def display_profile_pxx(profile_idx_to_show, current_ax, text_artist, horizontal_line_artist, 
                        current_active_profile_emas_dict, ordered_profile_names_list):
    
    # Ensure artists are valid before proceeding
    if not text_artist or not horizontal_line_artist:
        return
    if profile_idx_to_show >= len(ordered_profile_names_list):
        text_artist.set_text(f'Invalid profile index\nfor Pxx display')
        text_artist.set_visible(True)
        horizontal_line_artist.set_visible(False)
        return

    # Default horizontal line to invisible initially within this function
    horizontal_line_artist.set_visible(False)

    profile_name = ordered_profile_names_list[profile_idx_to_show]
    if profile_name not in current_active_profile_emas_dict:
        text_artist.set_text(f'{profile_name}\nData not available')
        text_artist.set_visible(True)
        current_ax.set_title(f"{profile_name} (Data N/A). Pausing...")
        return

    original_ema_series = current_active_profile_emas_dict[profile_name]
    percentile_val = get_percentile_from_name(profile_name)
    w_span_for_smoothing = get_w_value_from_name(profile_name)

    pxx_text_content = f'{profile_name}\nP{percentile_val} (of W{w_span_for_smoothing} smoothed EMA): '
    pxx_value_calculated = None 
    
    if not original_ema_series.empty:
        smoothed_for_pxx = original_ema_series.ewm(span=w_span_for_smoothing, adjust=False, ignore_na=True).mean().dropna()
        if not smoothed_for_pxx.empty:
            pxx_value_calculated = np.percentile(smoothed_for_pxx, percentile_val)
            pxx_text_content += f'{pxx_value_calculated:.2f} cores'
        else:
            pxx_text_content += 'N/A (smoothing error)'
    else:
        pxx_text_content += 'N/A (no base EMA)'
        
    text_artist.set_text(pxx_text_content)
    text_artist.set_visible(True)
    current_ax.set_title(f"{profile_name} (P{percentile_val} of W{w_span_for_smoothing} EMA shown). Pausing...")

    if pxx_value_calculated is not None:
        horizontal_line_artist.set_ydata([pxx_value_calculated, pxx_value_calculated])
        horizontal_line_artist.set_visible(True)

# --- Animation Functions ---
def init_animation_multi():
    global pxx_text_artist, pxx_horizontal_line
    line_raw_plot.set_data([], [])
    line_raw_plot.set_alpha(0.6)
    for line in profile_lines_plots:
        line.set_data([], [])
    scatter_reg_points.set_offsets(np.empty((0, 2)))
    line_reg_plot.set_data([], [])
    ax.set_title("Visualising nFit (C) 2025 Niël Lambrechts ...") # As per user's v2
    
    if pxx_text_artist is None:
        pxx_text_artist = ax.text(0.97, 0.97, '', ha='right', va='top',
                                 transform=ax.transAxes,
                                 fontsize=9, color='navy',
                                 bbox=dict(facecolor='aliceblue', alpha=0.85, boxstyle='round,pad=0.4'),
                                 visible=False, zorder=10)
    else:
        pxx_text_artist.set_text('')
        pxx_text_artist.set_visible(False)
    
    if pxx_horizontal_line is None:
        # Initialize with a default y that's likely off-screen or 0, and invisible
        pxx_horizontal_line = ax.axhline(y=0, color='darkslateblue', linestyle='--', linewidth=1.3, 
                                         visible=False, zorder=6, alpha=0.7)
    else:
        pxx_horizontal_line.set_ydata([0,0]) # Reset to avoid showing old line briefly
        pxx_horizontal_line.set_visible(False)

    ax.legend(loc='upper left', fontsize='small', title='Legend Details',
              frameon=True, facecolor='ivory', edgecolor='black',
              shadow=True, borderpad=0.7)
    all_artists = [line_raw_plot] + profile_lines_plots + [scatter_reg_points, line_reg_plot, pxx_text_artist, pxx_horizontal_line]
    return all_artists

def update_animation_multi(frame_num):
    global pxx_text_artist, pxx_horizontal_line
    
    if pxx_text_artist: pxx_text_artist.set_visible(False)
    if pxx_horizontal_line: pxx_horizontal_line.set_visible(False) # Hide Pxx line by default
    
    all_artists_to_return = [line_raw_plot] 
    all_artists_to_return.extend(profile_lines_plots)
    all_artists_to_return.extend([scatter_reg_points, line_reg_plot])
    if pxx_text_artist: all_artists_to_return.append(pxx_text_artist)
    if pxx_horizontal_line: all_artists_to_return.append(pxx_horizontal_line)

    current_phase_frame_counter = 0
    # --- Phase 1: Draw Raw Data ---
    if frame_num < current_phase_frame_counter + frames_raw_data:
        phase_specific_frame = frame_num - current_phase_frame_counter
        current_idx = int(((phase_specific_frame + 1) / frames_raw_data) * total_points)
        current_idx = min(current_idx, total_points)
        line_raw_plot.set_data(time_days[:current_idx], raw_cpu_data[:current_idx])
        line_raw_plot.set_alpha(0.6)
        if current_idx > 0:
            ax.set_title(f"Raw NMON Data (Day {int(time_days[current_idx-1]):d})")
        if frame_num == current_phase_frame_counter + frames_raw_data - 1:
             ax.set_title("Raw NMON Data Rendered. Pausing...")
        return all_artists_to_return
    current_phase_frame_counter += frames_raw_data

    # --- Delay 1: After Raw Data ---
    if frame_num < current_phase_frame_counter + frames_delay_after_raw:
        line_raw_plot.set_data(time_days, raw_cpu_data)
        line_raw_plot.set_alpha(0.5)
        ax.set_title("Raw NMON Data (unsmoothed, w/outliers)") # As per user's v2
        return all_artists_to_return
    current_phase_frame_counter += frames_delay_after_raw

    # --- Phase 2: Draw EMA Profile Lines Sequentially (with inter-EMA delays) ---
    plotted_ema_idx = 0 
    for profile_idx_ordered in range(len(active_profile_names_ordered)):
        profile_name_current_ordered = active_profile_names_ordered[profile_idx_ordered]
        if profile_name_current_ordered not in active_profile_emas:
            continue

        current_plot_line = profile_lines_plots[plotted_ema_idx]
        current_ema_data = active_profile_emas[profile_name_current_ordered]

        # A. Drawing the current EMA line
        start_frame_ema_drawing_phase = current_phase_frame_counter
        end_frame_ema_drawing_phase = start_frame_ema_drawing_phase + frames_per_profile_ema
        if frame_num < end_frame_ema_drawing_phase:
            line_raw_plot.set_alpha(0.25)
            for i in range(plotted_ema_idx):
                prev_profile_name_ordered = active_profile_names_ordered[i]
                if prev_profile_name_ordered in active_profile_emas: # Check if actually active
                    # Find the correct index for profile_lines_plots for these previous lines
                    # This needs to be robust if some profiles in active_profile_names_ordered were skipped
                    # However, the current logic for profile_lines_plots creation should align with plotted_ema_idx
                    profile_lines_plots[i].set_data(time_days, active_profile_emas[prev_profile_name_ordered])
                    profile_lines_plots[i].set_alpha(0.6)
            frame_in_current_ema_draw = frame_num - start_frame_ema_drawing_phase
            current_idx = int(((frame_in_current_ema_draw + 1) / frames_per_profile_ema) * total_points)
            current_idx = min(current_idx, total_points)
            current_plot_line.set_data(time_days[:current_idx], current_ema_data[:current_idx])
            current_plot_line.set_alpha(0.9)
            ax.set_title(f"nFit Exponential Moving Average (EMA) - {profile_name_current_ordered}") # As per user's v2
            return all_artists_to_return
        current_phase_frame_counter += frames_per_profile_ema

        # B. Pause between drawing different EMA lines (if this is not the last *plotted* EMA)
        if plotted_ema_idx < num_active_profiles - 1:
            start_frame_ema_delay_phase = current_phase_frame_counter
            end_frame_ema_delay_phase = start_frame_ema_delay_phase + frames_delay_between_emas
            if frame_num < end_frame_ema_delay_phase:
                current_plot_line.set_data(time_days, current_ema_data)
                current_plot_line.set_alpha(0.7)
                display_profile_pxx(profile_idx_ordered, ax, pxx_text_artist, pxx_horizontal_line, active_profile_emas, active_profile_names_ordered)
                # Title is set by display_profile_pxx
                return all_artists_to_return
            current_phase_frame_counter += frames_delay_between_emas
        plotted_ema_idx += 1
    
    # --- Delay 2: After all EMAs are drawn (and Pxx for the last EMA) ---
    if frame_num < current_phase_frame_counter + frames_delay_after_all_emas :
        line_raw_plot.set_alpha(0.2)
        idx_for_plot_obj = 0
        for name_ord in active_profile_names_ordered:
            if name_ord in active_profile_emas:
                profile_lines_plots[idx_for_plot_obj].set_data(time_days, active_profile_emas[name_ord])
                profile_lines_plots[idx_for_plot_obj].set_alpha(0.5)
                idx_for_plot_obj +=1
        
        if num_active_profiles > 0:
            last_active_profile_original_idx = -1
            for i in range(len(active_profile_names_ordered) -1, -1, -1):
                if active_profile_names_ordered[i] in active_profile_emas:
                    last_active_profile_original_idx = i
                    break
            if last_active_profile_original_idx != -1:
                # Find the corresponding plot line object and set its alpha
                temp_plotted_idx = 0
                final_plot_obj_idx = -1
                for i_ord_check in range(last_active_profile_original_idx + 1): # Iterate up to the identified last active profile
                    if active_profile_names_ordered[i_ord_check] in active_profile_emas:
                        if i_ord_check == last_active_profile_original_idx:
                            final_plot_obj_idx = temp_plotted_idx # This is the index in profile_lines_plots
                        temp_plotted_idx +=1
                
                if final_plot_obj_idx != -1 and final_plot_obj_idx < len(profile_lines_plots):
                     profile_lines_plots[final_plot_obj_idx].set_alpha(0.7) # Make it prominent for Pxx display

                display_profile_pxx(last_active_profile_original_idx, ax, pxx_text_artist, pxx_horizontal_line, active_profile_emas, active_profile_names_ordered)
        else:
             ax.set_title("Exponential Moving Averages (EMAs) completed.") # As per user's v2
        return all_artists_to_return
    current_phase_frame_counter += frames_delay_after_all_emas

    # --- Phase 3: Linear Regression Buildup ---
    if frame_num < current_phase_frame_counter + frames_regression_buildup:
        regression_phase_frame_num = frame_num - current_phase_frame_counter
        line_raw_plot.set_alpha(0.1)
        idx_for_plot_obj = 0
        last_active_profile_name_for_reg_highlight = None
        if num_active_profiles > 0:
             for name_ord_rev in reversed(active_profile_names_ordered):
                 if name_ord_rev in active_profile_emas:
                     last_active_profile_name_for_reg_highlight = name_ord_rev
                     break
        for name_ord in active_profile_names_ordered: # Iterate through all defined, check if active
            if name_ord in active_profile_emas:
                alpha_val = 0.15
                if name_ord == last_active_profile_name_for_reg_highlight:
                    alpha_val = 0.4
                if idx_for_plot_obj < len(profile_lines_plots): # Boundary check
                    profile_lines_plots[idx_for_plot_obj].set_alpha(alpha_val)
                idx_for_plot_obj += 1
        num_daily_points_to_show = min(len(daily_time_points), regression_phase_frame_num + 1)
        if num_daily_points_to_show > 0:
            current_daily_times_for_reg = np.array(daily_time_points[:num_daily_points_to_show])
            current_daily_values_for_reg = np.array(daily_p95_values[:num_daily_points_to_show])
            scatter_reg_points.set_offsets(np.c_[current_daily_times_for_reg, current_daily_values_for_reg])
            if num_daily_points_to_show > 1:
                slope, intercept = np.polyfit(current_daily_times_for_reg, current_daily_values_for_reg, 1)
                reg_line_y_values = slope * current_daily_times_for_reg + intercept
                line_reg_plot.set_data(current_daily_times_for_reg, reg_line_y_values)
                line_reg_plot.set_label(f'Linear Regression (Slope={slope:.3f} cores/day)')
                ax.legend(loc='upper left', fontsize='small', title='Legend Details',
                          frameon=True, facecolor='ivory', edgecolor='black',
                          shadow=True, borderpad=0.7)
                ax.set_title(f"Regression on Daily P95s (Day {int(current_daily_times_for_reg[-1])})")
            else:
                line_reg_plot.set_data([],[])
                ax.set_title(f"Linear Regression Trend (daily P95 data points)...")
        if frame_num == current_phase_frame_counter + frames_regression_buildup - 1:
            ax.set_title("Completed Linear Regression Trend. Pausing...")
        return all_artists_to_return
    current_phase_frame_counter += frames_regression_buildup

    # --- Final Pause Phase Before Loop ---
    if frame_num < current_phase_frame_counter + frames_for_loop_pause:
        if frame_num == current_phase_frame_counter:
             ax.set_title(f"Animation Complete. Repeating in {pause_duration_before_repeat_ms/1000:.0f}s...")
        return all_artists_to_return
    
    return all_artists_to_return

# --- Create Animation ---
ani_multi = None
is_animation_paused = False
def toggle_pause_resume(event):
    global is_animation_paused, ani_multi, bpause 
    if is_animation_paused:
        ani_multi.event_source.start()
        bpause.label.set_text('Pause')
        is_animation_paused = False
    else:
        ani_multi.event_source.stop()
        bpause.label.set_text('Resume')
        is_animation_paused = True
    fig.canvas.draw_idle()

ax_button_pause = plt.axes([0.40, 0.025, 0.1, 0.05])
bpause = Button(ax_button_pause, 'Pause', color='lightgoldenrodyellow', hovercolor='0.975')
bpause.on_clicked(toggle_pause_resume)
ani_multi = animation.FuncAnimation(fig, update_animation_multi, frames=total_frames_for_animation,
                                    init_func=init_animation_multi, blit=False, 
                                    interval=animation_interval_ms, 
                                    repeat=True, 
                                    repeat_delay=0 
                                    )
plt.subplots_adjust(left=0.1, right=0.9, top=0.9, bottom=0.2)
plt.show()
