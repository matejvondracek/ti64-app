%% Specify Miller Indices

h = { ...
  Miller(2,1,1,CS),...
  Miller(1,0,3,CS),...
  Miller(1,1,0,CS),...
  };

%% Import the Data

% create a Pole Figure variable containing the data
pf = PoleFigure.load(fname,h,CS,SS,'interface','xrdml');

% background
pf_bg = PoleFigure.load(fname_bg,h,CS,SS,'interface','xrdml');

% correct data
pf = correct(pf,'bg',pf_bg);

odf = calcODF(pf);

%Evaluate pole figure densities on a common grid
r = plotS2Grid('resolution',degree);

pf1 = calcPoleFigure(odf,Miller(0,0,0,1,CS),r);
pf2 = calcPoleFigure(odf,Miller(1,0,-1,0,CS),r);
pf3 = calcPoleFigure(odf,Miller(1,1,-2,0,CS),r);

max1 = max(pf1.intensities(:));
max2 = max(pf2.intensities(:));
max3 = max(pf3.intensities(:));

min1 = min(pf1.intensities(:));
min2 = min(pf2.intensities(:));
min3 = min(pf3.intensities(:));

cmax = max([max1 max2 max3]);
cmin = min([min1 min2 min3]);

rot1 = rotation.byAxisAngle(xvector,90*degree);
rot2 = rotation.byAxisAngle(yvector,90*degree);
rot3 = rotation.byAxisAngle(zvector,90*degree);

if rot_axis == "x"
    odf = rotate(odf, rot1);
elseif rot_axis == "y"
    odf = rotate(odf, rot2);
elseif rot_axis == "z"
    odf = rotate(odf, rot3);
end

odf = rotate(odf, rot3);


%% (0001) PF
fig = figure; 
plotPDF(odf,Miller(0,0,0,1,CS),'antipodal','resolution',degree); 
delete(findall(gcf,'Type','text','String','X'))
delete(findall(gcf,'Type','text','String','Y'))
delete(findall(gcf,'Type','text','String','Z'))
if rot_axis == "x"
    annotate([xvector,-yvector,zvector],'label',{x_label,y_label,z_label},'BackgroundColor','w');
else
    annotate([xvector,yvector,zvector],'label',{x_label,y_label,z_label},'BackgroundColor','w');
end
cb = mtexColorbar;
cb.Label.FontSize = 20;
cb.Color = 'k';
cb.FontSize = 20;
cb.Ticks = linspace(cmin+0.001,cmax-0.001,8);
cb.TickLabels = compose('%.2f', cb.Ticks);
cb.FontName = "CMU Serif";
setColorRange('equal'); 
clim([cmin cmax]);
colormap(turbo); 

tit = mtexTitle('$\left( 0001 \right)$','Interpreter','latex');
tit.FontSize = 20;

% Save MATLAB figure file 
savefig(fig, path + "_PF_(0001)_" + x_label + y_label + z_label + ".fig") 

% Save PNG (high resolution) 
exportgraphics(fig, path + "_PF_(0001)_" + x_label + y_label + z_label + ".png",'Resolution',300) 
close(fig)

if false
    %% prismatic planes PF
    fig = figure; 
    % Starting basal direction [10-10]
    h0 = Miller(1,0,-1,0,CS);

    % Rotations in the basal plane
    angles = (0:2:58)*degree;

    % Initialize combined pole figure
    pf_sum = 0;

    for i = 1:length(angles)

        % Rotation around the c-axis
        R = rotation.byAxisAngle(vector3d.Z, angles(i));

        % Rotated direction
        h = R * h0;

        % Calculate pole figure density
        pf = calcPDF(odf,h);

        % Add contribution
        pf_sum = pf_sum + pf;

    end

    % Normalize intensity
    pf_sum = pf_sum ./ max(pf_sum);
    plot(pf_sum,'antipodal','resolution',degree)
    setColorRange('equal'); 
    if rot_axis == "x"
        annotate([xvector,-yvector,zvector],'label',{x_label,y_label,z_label},'BackgroundColor','w');
        cb = mtexColorbar;
        cb.Label.FontSize = 20;
        cb.Color = 'k';
        cb.FontSize = 20;
        cb.Ticks = linspace(cmin+0.001,cmax-0.001,8);
        if DED == true
            cb.TickLabels = strrep(compose('%.1f', cb.Ticks), '.', ',');
        else
            cb.TickLabels = strrep(compose('%.2f', cb.Ticks), '.', ',');
        end
        cb.FontName = "CMU Serif";
    else
        annotate([xvector,yvector,zvector],'label',{x_label,y_label,z_label},'BackgroundColor','w');
    end
    clim([cmin cmax]);
    colormap(turbo); 
    tit = mtexTitle('$\vec{n} \perp \langle 0001 \rangle$','Interpreter','latex');
    tit.FontSize = 20;

    % Save MATLAB figure file 
    savefig(fig, path + "_PF_prism_" + x_label + y_label + z_label + ".fig") 

    % Save PNG (high resolution) 
    exportgraphics(fig, path + "_PF_prism_" + x_label + y_label + z_label + ".png",'Resolution',150)
    close(fig)


    %% (11-20) PF
    fig = figure; 
    plotPDF(odf,Miller(1,1,-2,0,CS),'antipodal','resolution',degree); 
    delete(findall(gcf,'Type','text','String','X'))
    delete(findall(gcf,'Type','text','String','Y'))
    delete(findall(gcf,'Type','text','String','Z'))
    if rot_axis == "x"
        annotate([xvector,-yvector,zvector],'label',{x_label,y_label,z_label},'BackgroundColor','w');
    else
        annotate([xvector,yvector,zvector],'label',{x_label,y_label,z_label},'BackgroundColor','w');
    end
    setColorRange('equal'); 
    mtexColorbar;
    clim([cmin cmax]);
    colormap(turbo); 

    % Save MATLAB figure file 
    savefig(fig, path + "_PF_(11-20)_" + x_label + y_label + z_label + ".fig") 

    % Save PNG (high resolution) 
    exportgraphics(fig, path + "_PF_(11-20)_" + x_label + y_label + z_label + ".png",'Resolution',300) 
    close(fig)

    %% (10-10) PF
    fig = figure; 
    plotPDF(odf,Miller(1,0,-1,0,CS),'antipodal','resolution',degree); 
    delete(findall(gcf,'Type','text','String','X'))
    delete(findall(gcf,'Type','text','String','Y'))
    delete(findall(gcf,'Type','text','String','Z'))
    if rot_axis == "x"
        annotate([xvector,-yvector,zvector],'label',{x_label,y_label,z_label},'BackgroundColor','w');
    else
        annotate([xvector,yvector,zvector],'label',{x_label,y_label,z_label},'BackgroundColor','w');
    end
    setColorRange('equal'); 
    mtexColorbar;
    clim([cmin cmax]);
    colormap(turbo); 

    % Save MATLAB figure file 
    savefig(fig, path + "_PF_(10-10)_" + x_label + y_label + z_label + ".fig") 

    % Save PNG (high resolution) 
    exportgraphics(fig, path + "_PF_(10-10)_" + x_label + y_label + z_label + ".png",'Resolution',600) 
    close(fig)
end

%% IPF
if rot_axis == "none"
    fig = figure;

    plotIPDF(odf,[yvector,xvector,zvector],'antipodal');

    setColorRange('equal');
    cb = mtexColorbar;
    cb.Label.FontSize = 30;
    cb.Color = 'k';
    cb.FontSize = 30;
    cb.LimitsMode = 'auto';
    cb.TicksMode = 'auto';
    %cb.Ticks = linspace(cmin/2+0.001,cmax*2-0.001,12);
    %cb.TickLabels = strrep(compose('%.1f', cb.Ticks), '.', ',');
    cb.FontName = "CMU Serif";
    colormap(turbo); 

    % Get all text objects
    txt = findall(gcf,'Type','text');

    for k = 1:numel(txt)

        s = txt(k).String;

        % Handle cell/string cases
        if iscell(s)
            s = s{1};
        end

        txt(k).FontSize = 30;

        if contains(s,'x')
            txt(k).String = x_label;

        elseif contains(s,'y')
            txt(k).String = y_label;

        elseif contains(s,'z')
            txt(k).String = z_label;
        end
    end

    
    tit = mtexTitle(z_label);
    tit.FontSize = 30;

    % Save MATLAB figure file 
    savefig(fig, path + "_IPF.fig") 

    % Save PNG (high resolution) 
    exportgraphics(fig, path + "_IPF.png",'Resolution',300) 
    close(fig)
end