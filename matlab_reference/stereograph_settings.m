fig = figure('Visible','off','Color','w');

[X,Y] = meshgrid(linspace(min(P(:,1)),max(P(:,1)),1000), ...
                 linspace(min(P(:,2)),max(P(:,2)),1000));

cmin = min(E_all);
cmax = max(E_all);

Z = griddata(P(:,1),P(:,2),E_all,X,Y);

% --- Mask invalid (NaN) regions ---
Z_masked = Z;
Z_masked(isnan(Z_masked)) = NaN;

% --- Plot ---
h = imagesc([min(P(:,1)) max(P(:,1))], ...
            [min(P(:,2)) max(P(:,2))], Z_masked);

set(gca,'YDir','normal')
axis equal
axis tight
axis off
hold on

% Make NaN transparent
set(h, 'AlphaData', ~isnan(Z_masked))

% --- Light dotted cross lines ---
x_mid = mean([min(P(:,1)) max(P(:,1))]);
y_mid = mean([min(P(:,2)) max(P(:,2))]);

x = linspace(min(P(:,1)), max(P(:,1)), 100);   % increase/decrease for spacing
y = y_mid * ones(size(x));

plot(x, y, 'k.', 'MarkerSize', 5)

y = linspace(min(P(:,2)), max(P(:,2)), 100);
x = x_mid * ones(size(y));

plot(x, y, 'k.', 'MarkerSize', 5)

% --- Offsets based on data range ---
dx = 0.01 * (max(P(:,1)) - min(P(:,1)));
dy = 0.01 * (max(P(:,2)) - min(P(:,2)));

% --- Direction labels (shifted outward) ---
if label == false
    text(x_mid, max(P(:,2)) + dy, y_label, ...
        'HorizontalAlignment','center','VerticalAlignment','bottom', ...
        'Color','k','FontWeight','bold','FontSize',20, 'FontName', "CMU Serif")
else
    text(x_mid, min(P(:,2)) - 0*dy, y_label, ...
        'HorizontalAlignment','center','VerticalAlignment','top', ...
        'Color','k','FontWeight','bold','FontSize',20, 'FontName', "CMU Serif")
end

text(max(P(:,1)) + 2*dx, y_mid + dy, x_label, ...
    'HorizontalAlignment','left','VerticalAlignment','middle', ...
    'Color','k','FontWeight','bold','FontSize',20, 'FontName', "CMU Serif")

% --- Center label (shifted slightly up-left) ---
scale = 2;
if z_label == "T2"
    scale = 4;
end

text(x_mid - dx*scale, y_mid + dy*3, z_label, ...
    'HorizontalAlignment','center','Color','w','FontWeight','bold','FontSize',20,'FontName', "CMU Serif")

if label == true
    % --- Colorbar ---
    cb = colorbar;
    cb.Label.String = 'E (GPa)';
    cb.Label.FontSize = 20;
    cb.Label.FontName = "CMU Serif";
    cb.Color = 'k';
    cb.FontSize = 20;
    cb.Position = [0.8 0.08 0.03 0.84];
    cb.Ticks = linspace(cmin+0.001,cmax-0.001,8);
    cb.TickLabels = strrep(compose('%.1f', cb.Ticks), '.', ',');
    cb.FontName = "CMU Serif";
end
% --- Colormap ---
clim([cmin cmax]);
colormap(turbo(1024));

% Fill figure nicely
set(gca,'Position',[0.05 0.05 0.85 0.9])

% Save MATLAB figure file
savefig(fig, path + "_PF_" + x_label + y_label + z_label + ".fig")

% Save PNG (high resolution)
exportgraphics(fig, path + "_PF_" + x_label + y_label + z_label + ".png",'Resolution',150)

close(fig)